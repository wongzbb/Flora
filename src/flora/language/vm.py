# SPDX-License-Identifier: Apache-2.0
"""Deterministic JSON interpreter with explicit calls and effect continuations.

There is no Python eval/exec, host-global access, model call, or tool execution in
this module. An effect is a suspended request. Alternative children are isolated
internal machine states; selecting and committing one is a caller responsibility.
"""

from __future__ import annotations

import json
import math
import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from flora.language.ir import parse_program
from flora.state.opaque import OPAQUE_KEY, contains_opaque
from flora.support.errors import MachineStateError, ValidationError
from flora.support.values import MAX_INTEGER_BITS, bounded_clone, canonical_json, clone

MAX_STACK = 128
MODES = {"observed", "hypothetical", "replay"}
STATUSES = {"running", "effect", "alternative", "replan", "returned", "fault", "unknown"}


@dataclass
class Machine:
    program: dict
    block_id: str
    registers: dict
    mode: str = "observed"
    call_stack: list[dict] = field(default_factory=list)
    op_index: int = 0
    status: str = "running"
    pending: dict | None = None
    value: Any = None
    details: dict = field(default_factory=dict)
    steps: int = 0

    def to_dict(self) -> dict:
        return clone(
            {
                "program": self.program,
                "block_id": self.block_id,
                "registers": self.registers,
                "mode": self.mode,
                "call_stack": self.call_stack,
                "op_index": self.op_index,
                "status": self.status,
                "pending": self.pending,
                "value": self.value,
                "details": self.details,
                "steps": self.steps,
            }
        )

    @classmethod
    def from_dict(cls, data: dict) -> Machine:
        data = clone(data)
        expected = {
            "program",
            "block_id",
            "registers",
            "mode",
            "call_stack",
            "op_index",
            "status",
            "pending",
            "value",
            "details",
            "steps",
        }
        if type(data) is not dict or set(data) != expected:
            raise MachineStateError("serialized machine has incorrect fields")
        data["program"] = parse_program(data["program"])
        blocks = data["program"]["blocks"]
        if type(data["block_id"]) is not str or data["block_id"] not in blocks:
            raise MachineStateError("machine has an unknown block")
        if type(data["mode"]) is not str or data["mode"] not in MODES:
            raise MachineStateError("machine mode must be observed, hypothetical, or replay")
        if type(data["status"]) is not str or data["status"] not in STATUSES:
            raise MachineStateError("unknown machine status")
        block = blocks[data["block_id"]]
        index = data["op_index"]
        if type(index) is not int or not 0 <= index <= len(block["ops"]):
            raise MachineStateError("invalid operation index")
        names = set(block["params"]) | {item["dest"] for item in block["ops"][:index]}
        if type(data["registers"]) is not dict or set(data["registers"]) != names:
            raise MachineStateError("registers do not match the current block and operation index")
        if type(data["steps"]) is not int or data["steps"] < 0:
            raise MachineStateError("steps must be a nonnegative integer")
        if type(data["details"]) is not dict:
            raise MachineStateError("machine details must be an object")
        stack = data["call_stack"]
        if type(stack) is not list or len(stack) > MAX_STACK:
            raise MachineStateError("invalid or excessive call stack")
        for frame in stack:
            _validate_frame(frame, blocks)
        pending = data["pending"]
        if data["status"] == "effect":
            if type(pending) is not dict or set(pending) != {"frame", "request"}:
                raise MachineStateError("effect suspension requires frame and request")
            _validate_frame(pending["frame"], blocks)
            request = pending["request"]
            if (
                type(request) is not dict
                or set(request) != {"tool", "args"}
                or type(request["tool"]) is not str
                or not request["tool"]
                or type(request["args"]) is not dict
            ):
                raise MachineStateError("invalid suspended effect request")
            if index != len(block["ops"]) or block["term"]["op"] != "effect":
                raise MachineStateError("effect status does not match its block")
            term = block["term"]
            expected_request = {
                "tool": term["tool"],
                "args": _eval(term["args"], data["registers"]),
            }
            expected_frame = {
                "resume": term["resume"],
                "bind": term["bind"],
                "capture": _eval_bindings(term["capture"], data["registers"]),
            }
            if canonical_json(request) != canonical_json(expected_request) or canonical_json(
                pending["frame"]
            ) != canonical_json(expected_frame):
                raise MachineStateError("suspended request/capture does not match its IR")
        elif data["status"] == "replan":
            if pending is not None or index != len(block["ops"]) or block["term"]["op"] != "replan":
                raise MachineStateError("replan status does not match its block")
            term = block["term"]
            expected_value = {
                "reason": _eval(term["reason"], data["registers"]),
                "state": _eval(term["state"], data["registers"]),
            }
            if (
                type(expected_value["reason"]) is not str
                or not expected_value["reason"].strip()
                or type(expected_value["state"]) is not dict
                or canonical_json(data["value"]) != canonical_json(expected_value)
            ):
                raise MachineStateError("replan payload does not match its IR")
        elif data["status"] == "alternative":
            if (
                pending is not None
                or index != len(block["ops"])
                or block["term"]["op"] != "alternative"
            ):
                raise MachineStateError("alternative status does not match its block")
        elif pending is not None:
            raise MachineStateError("only an effect suspension may contain pending data")
        return cls(**data)


@dataclass
class Boundary:
    kind: str
    machine: Machine
    request: dict | None = None
    value: Any = None
    details: dict = field(default_factory=dict)
    alternatives: list[Machine] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "machine": self.machine.to_dict(),
            "request": clone(self.request),
            "value": clone(self.value),
            "details": clone(self.details),
            "alternatives": [child.to_dict() for child in self.alternatives],
        }


def _validate_frame(frame: dict, blocks: dict) -> None:
    if type(frame) is not dict or set(frame) != {"resume", "bind", "capture"}:
        raise MachineStateError("invalid continuation frame")
    if type(frame["resume"]) is not str or frame["resume"] not in blocks:
        raise MachineStateError("unknown continuation block")
    if type(frame["bind"]) is not str or type(frame["capture"]) is not dict:
        raise MachineStateError("invalid continuation bindings")
    if frame["bind"] in frame["capture"]:
        raise MachineStateError("continuation capture shadows its bind")
    if set(blocks[frame["resume"]]["params"]) != set(frame["capture"]) | {frame["bind"]}:
        raise MachineStateError("continuation bindings do not match block parameters")


def new_machine(program: dict, inputs: dict | None = None, *, mode: str = "observed") -> Machine:
    program = parse_program(program)
    values = clone({} if inputs is None else inputs)
    if type(values) is not dict or set(values) != set(
        program["blocks"][program["entry"]]["params"]
    ):
        raise MachineStateError("input keys must exactly match entry parameters")
    if type(mode) is not str or mode not in MODES:
        raise MachineStateError("unknown machine mode")
    return Machine(program, program["entry"], values, mode=mode)


def _eval(expression: Any, registers: dict) -> Any:
    if type(expression) is dict:
        if set(expression) == {"var"}:
            return registers[expression["var"]]
        if set(expression) == {"literal"}:
            return expression["literal"]
        return {key: _eval(value, registers) for key, value in expression.items()}
    if type(expression) is list:
        return [_eval(value, registers) for value in expression]
    return expression


def _eval_bindings(bindings: dict, registers: dict) -> dict:
    """Parameter names may themselves be 'var' or 'literal'."""
    return {name: _eval(expression, registers) for name, expression in bindings.items()}


class _Fault(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


class _Unknown(_Fault):
    pass


def _expect(condition: bool, message: str, code: str = "TYPE_ERROR") -> None:
    if not condition:
        raise _Fault(code, message)


def _boolean(value: Any) -> bool:
    if contains_opaque(value):
        raise _Unknown("OPAQUE_VALUE", "an opaque handle has no interpreter-visible truth value")
    _expect(type(value) is bool, "boolean value required")
    return value


def _number(value: Any) -> int | float:
    _expect(type(value) in (int, float), "numeric value required (booleans are not numbers)")
    return value


def _lookup(container: Any, key: Any) -> tuple[bool, Any]:
    if type(container) is dict:
        _expect(type(key) is str, "object key must be a string")
        return key in container, container.get(key)
    if container is None:
        raise _Fault(
            "TYPE_ERROR",
            "get requires an object, array, or string; the observed value is null: "
            "branch on result_available/status before reading a pending child result",
        )
    _expect(type(container) in (list, str), "get requires an object, array, or string")
    _expect(type(key) is int, "sequence index must be an integer")
    if -len(container) <= key < len(container):
        return True, container[key]
    return False, None


def _equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return set(left) == set(right) and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _allocation_check(values: list[Any], maximum: int) -> None:
    # Upper bound before concatenation: repeated large strings/arrays must not
    # allocate first and discover the limit afterwards.
    total = 0
    for value in values:
        total += len(canonical_json(value).encode("utf-8"))
        if total > maximum:
            raise _Fault("VALUE_LIMIT", "operation would exceed the allocation budget")


def _apply(op: str, args: list[Any], receipts: Sequence[dict], memory: dict, maximum: int) -> Any:
    if op == "const":
        return args[0]
    # A handle may be transported in JSON containers and explicit continuations,
    # but its carrier fields are not object state. Inspecting the marker itself
    # or comparing any structure containing handles has an unknown result.
    shallow_ops = {
        "get",
        "get_default",
        "has",
        "set",
        "delete",
        "keys",
        "values",
        "length",
        "type",
        "append",
        "extend",
        "concat",
        "slice",
    }
    if op in shallow_ops:
        inspected = args[:1]
        if op in {"get", "get_default", "has", "set", "delete"} and contains_opaque(args[1]):
            raise _Unknown("OPAQUE_VALUE", "opaque handles cannot be interpreted as keys")
        if op in {"extend", "concat"}:
            inspected = args
        if any(type(item) is dict and OPAQUE_KEY in item for item in inspected):
            raise _Unknown(
                "OPAQUE_VALUE",
                "opaque carrier metadata is not an observable object state",
            )
        if op == "slice" and any(contains_opaque(item) for item in args[1:]):
            raise _Unknown("OPAQUE_VALUE", "opaque handles cannot be interpreted as slice bounds")
    elif op == "read_memory":
        if args and contains_opaque(args[0]):
            raise _Unknown("OPAQUE_VALUE", "opaque handles cannot be interpreted as memory keys")
    elif any(contains_opaque(item) for item in args):
        raise _Unknown("OPAQUE_VALUE", "this operation cannot interpret opaque object state")
    if op in ("get", "get_default", "has"):
        exists, value = _lookup(args[0], args[1])
        if op == "has":
            return exists
        if not exists:
            if op == "get_default":
                return args[2]
            detail = "requested key or index is absent"
            if (
                isinstance(args[0], dict)
                and args[0].get("result_available") is False
            ):
                detail += (
                    "; this child result is unavailable: branch on result_available/status "
                    "and wait or collect another page before reading result fields"
                )
            elif isinstance(args[0], dict) and "agent_id" in args[0]:
                detail += (
                    "; this value is a child identity envelope: use only its opaque "
                    "agent_id with wait/read/review, never metadata as a child result"
                )
            elif args[0] is None:
                detail += (
                    "; the observed value is null: branch on result_available/status "
                    "before reading a pending child result"
                )
            raise _Fault("MISSING_KEY", detail)
        return value
    if op in ("set", "delete"):
        original, key = args[:2]
        _expect(type(original) in (dict, list), "set/delete require an object or array")
        exists, _ = _lookup(original, key)
        result = clone(original)
        if op == "delete":
            _expect(exists, "cannot delete a missing key or index", "MISSING_KEY")
            del result[key]
        else:
            if type(result) is list:
                _expect(exists, "cannot set an out-of-range array index", "MISSING_KEY")
            result[key] = clone(args[2])
        return result
    if op in ("keys", "values"):
        _expect(type(args[0]) is dict, "keys/values require an object")
        keys = sorted(args[0])
        return keys if op == "keys" else [args[0][key] for key in keys]
    if op == "length":
        _expect(
            type(args[0]) in (dict, list, str),
            "length requires object, array, or string",
        )
        return len(args[0])
    if op in ("append", "extend"):
        _expect(type(args[0]) is list, "append/extend require an array")
        if op == "extend":
            _expect(type(args[1]) is list, "extend requires two arrays")
        _allocation_check(args, maximum)
        return args[0] + ([args[1]] if op == "append" else args[1])
    if op == "slice":
        _expect(type(args[0]) in (list, str), "slice requires array or string")
        _expect(
            all(item is None or type(item) is int for item in args[1:]),
            "slice bounds must be integers or null",
        )
        bounds = args[1:] + [None] * (4 - len(args))
        _expect(bounds[2] != 0, "slice step cannot be zero", "ARITHMETIC_ERROR")
        return args[0][slice(*bounds)]
    if op in ("add", "sub", "mul", "div", "mod"):
        left, right = (_number(item) for item in args)
        if op == "mul" and type(left) is int and type(right) is int:
            _expect(
                left.bit_length() + right.bit_length() <= MAX_INTEGER_BITS + 1,
                "integer multiplication exceeds bit limit",
                "VALUE_LIMIT",
            )
        if op in ("div", "mod"):
            _expect(right != 0, "division by zero", "ARITHMETIC_ERROR")
        operations = {
            "add": operator.add,
            "sub": operator.sub,
            "mul": operator.mul,
            "div": operator.truediv,
            "mod": operator.mod,
        }
        result = operations[op](left, right)
        if type(result) is float:
            _expect(
                math.isfinite(result),
                "non-finite arithmetic result",
                "ARITHMETIC_ERROR",
            )
        return result
    if op in ("eq", "ne"):
        equal = _equal(args[0], args[1])
        return equal if op == "eq" else not equal
    if op in ("lt", "le", "gt", "ge"):
        left, right = args
        _expect(
            (type(left) in (int, float) and type(right) in (int, float))
            or type(left) is type(right) is str,
            "comparison requires two numbers or two strings",
        )
        return {
            "lt": operator.lt,
            "le": operator.le,
            "gt": operator.gt,
            "ge": operator.ge,
        }[op](left, right)
    if op in ("and", "or", "not"):
        values = [_boolean(item) for item in args]
        return not values[0] if op == "not" else (all(values) if op == "and" else any(values))
    if op == "concat":
        kind = type(args[0])
        _expect(
            kind in (str, list) and all(type(item) is kind for item in args),
            "concat requires only strings or only arrays",
        )
        _allocation_check(args, maximum)
        if kind is str:
            return "".join(args)
        result = []
        for item in args:
            result.extend(item)
        return result
    if op == "contains":
        container, item = args
        _expect(
            type(container) in (dict, str, list),
            "contains requires object, string, or array",
        )
        if type(container) in (dict, str):
            _expect(type(item) is str, "object/string membership requires a string")
            return item in container
        return any(_equal(item, value) for value in container)
    if op == "to_string":
        return args[0] if type(args[0]) is str else canonical_json(args[0])
    if op == "parse_json":
        _expect(type(args[0]) is str, "parse_json requires a string")
        _expect(
            len(args[0].encode("utf-8")) <= maximum,
            "JSON input too large",
            "VALUE_LIMIT",
        )

        def reject_constant(value):
            raise ValueError(f"non-finite number {value}")

        def object_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate object key {key!r}")
                result[key] = value
            return result

        try:
            return json.loads(
                args[0], parse_constant=reject_constant, object_pairs_hook=object_pairs
            )
        except (ValueError, RecursionError) as error:
            raise _Fault("JSON_PARSE_ERROR", str(error)) from error
    if op == "read_receipt":
        index = args[0]
        _expect(type(index) is int, "receipt index must be an integer")
        if index < 0 or index >= len(receipts):
            raise _Unknown("RECEIPT_UNAVAILABLE", "no actual receipt at this index")
        record = receipts[index]
        if record.get("status") in ("pending", "interrupted_unknown"):
            raise _Unknown("RECEIPT_UNSETTLED", "receipt has no known settled outcome")
        return record
    if op == "read_memory":
        if not args:
            return memory
        _expect(type(args[0]) is str, "memory key must be a string")
        if args[0] in memory:
            return memory[args[0]]
        if len(args) == 2:
            return args[1]
        raise _Unknown("MEMORY_UNAVAILABLE", "requested memory key is absent")
    if op == "assert":
        if not _boolean(args[0]):
            message = args[1] if len(args) == 2 and type(args[1]) is str else "assertion failed"
            raise _Fault("ASSERTION_FAILED", message)
        return True
    if op == "type":
        return {
            type(None): "null",
            bool: "boolean",
            int: "integer",
            float: "number",
            str: "string",
            list: "array",
            dict: "object",
        }[type(args[0])]
    raise _Fault("UNKNOWN_OPERATION", op)


def _frame(term: dict, registers: dict, maximum: int) -> dict:
    return {
        "resume": term["resume"],
        "bind": term["bind"],
        "capture": bounded_clone(_eval_bindings(term["capture"], registers), maximum),
    }


def _enter(machine: Machine, target: str, arguments: dict) -> None:
    machine.block_id = target
    machine.registers = clone(arguments)
    machine.op_index = 0
    machine.status = "running"
    machine.pending = None
    machine.value = None
    machine.details = {}


def _deliver(machine: Machine, frame: dict, value: Any) -> None:
    values = clone(frame["capture"])
    values[frame["bind"]] = clone(value)
    _enter(machine, frame["resume"], values)


def _stopped(machine: Machine) -> Boundary:
    if machine.status == "effect":
        return Boundary("effect", machine, request=clone(machine.pending["request"]))
    if machine.status == "returned":
        return Boundary("return", machine, value=clone(machine.value))
    if machine.status == "replan":
        return Boundary("replan", machine, value=clone(machine.value))
    return Boundary(machine.status, machine, details=clone(machine.details))


def _failure(machine: Machine, kind: str, code: str, message: str) -> Boundary:
    machine.status = kind
    machine.pending = None
    machine.details = {
        "code": code,
        "message": message,
        "block": machine.block_id,
        "op_index": machine.op_index,
    }
    return Boundary(kind, machine, details=clone(machine.details))


def run_until_boundary(
    machine: Machine,
    *,
    receipts: Sequence[dict] = (),
    memory: dict | None = None,
    fuel: int = 50000,
    max_value_bytes: int = 1048576,
) -> Boundary:
    """Return a fresh state; the supplied machine and observed inputs are unchanged.

    Fuel exhaustion and unavailable observed data produce UNKNOWN, not success.
    No external request is dispatched. Receipt authenticity is the trace store's
    responsibility; this VM cannot certify arbitrary caller-supplied dictionaries.
    """
    if type(fuel) is not int or fuel < 0:
        raise ValidationError("fuel must be a nonnegative integer")
    if type(max_value_bytes) is not int or max_value_bytes <= 0:
        raise ValidationError("max_value_bytes must be positive")
    current = Machine.from_dict(machine.to_dict())
    actual_receipts = clone(list(receipts))
    actual_memory = clone({} if memory is None else memory)
    if type(actual_memory) is not dict or any(type(item) is not dict for item in actual_receipts):
        raise ValidationError("memory and every receipt must be objects")
    if current.status not in ("running", "alternative"):
        return _stopped(current)
    # Re-observing an alternative can recreate child states, never execute them.
    if current.status == "alternative":
        current.status = "running"
    try:
        bounded_clone(current.registers, max_value_bytes)
        bounded_clone(current.call_stack, max_value_bytes)
        for _ in range(fuel):
            block = current.program["blocks"][current.block_id]
            if current.op_index == 0:
                # A function return may combine independently bounded captures
                # and result data; check their combined register footprint.
                bounded_clone(current.registers, max_value_bytes)
            current.steps += 1
            if current.op_index < len(block["ops"]):
                operation = block["ops"][current.op_index]
                args = [_eval(expr, current.registers) for expr in operation["args"]]
                value = _apply(
                    operation["op"],
                    args,
                    actual_receipts,
                    actual_memory,
                    max_value_bytes,
                )
                value = bounded_clone(value, max_value_bytes)
                # Bound the whole register set as well as each individual value.
                registers = dict(current.registers)
                registers[operation["dest"]] = value
                current.registers = bounded_clone(registers, max_value_bytes)
                current.op_index += 1
                continue
            term = block["term"]
            op = term["op"]
            if op == "return":
                value = bounded_clone(_eval(term["value"], current.registers), max_value_bytes)
                if current.call_stack:
                    _deliver(current, current.call_stack.pop(), value)
                    continue
                current.status = "returned"
                current.value = value
                return _stopped(current)
            if op == "replan":
                reason = bounded_clone(_eval(term["reason"], current.registers), max_value_bytes)
                state = bounded_clone(_eval(term["state"], current.registers), max_value_bytes)
                _expect(
                    type(reason) is str and bool(reason.strip()),
                    "replan reason must be a nonempty string",
                )
                _expect(type(state) is dict, "replan state must be an object")
                current.value = bounded_clone({"reason": reason, "state": state}, max_value_bytes)
                current.status = "replan"
                return _stopped(current)
            if op in ("jump", "branch", "call"):
                arguments = bounded_clone(
                    _eval_bindings(term["args"], current.registers), max_value_bytes
                )
                if op == "branch":
                    condition = _boolean(_eval(term["condition"], current.registers))
                    target = term["yes"] if condition else term["no"]
                else:
                    target = term["target"]
                if op == "call":
                    _expect(
                        len(current.call_stack) < MAX_STACK,
                        "call stack limit reached",
                        "STACK_LIMIT",
                    )
                    current.call_stack = bounded_clone(
                        current.call_stack + [_frame(term, current.registers, max_value_bytes)],
                        max_value_bytes,
                    )
                _enter(current, target, arguments)
                continue
            if op == "effect":
                arguments = bounded_clone(_eval(term["args"], current.registers), max_value_bytes)
                _expect(
                    type(arguments) is dict,
                    "effect arguments must resolve to an object",
                )
                current.pending = {
                    "frame": _frame(term, current.registers, max_value_bytes),
                    "request": {"tool": term["tool"], "args": arguments},
                }
                current.status = "effect"
                return _stopped(current)
            if op == "alternative":
                _expect(
                    len(current.call_stack) < MAX_STACK,
                    "call stack limit reached",
                    "STACK_LIMIT",
                )
                arguments = bounded_clone(
                    _eval_bindings(term["args"], current.registers), max_value_bytes
                )
                frame = _frame(term, current.registers, max_value_bytes)
                stacked = bounded_clone(current.call_stack + [frame], max_value_bytes)
                current.status = "alternative"
                children = []
                for target in term["branches"]:
                    child = Machine.from_dict(current.to_dict())
                    child.call_stack = clone(stacked)
                    _enter(child, target, arguments)
                    children.append(child)
                return Boundary(
                    "alternative",
                    current,
                    alternatives=children,
                    details={"branches": list(term["branches"])},
                )
            raise _Fault("UNKNOWN_TERMINATOR", op)
    except _Unknown as error:
        return _failure(current, "unknown", error.code, error.message)
    except _Fault as error:
        return _failure(current, "fault", error.code, error.message)
    except ValidationError as error:
        return _failure(current, "fault", "VALUE_LIMIT", str(error))
    except (ArithmeticError, RecursionError) as error:
        return _failure(current, "fault", "ARITHMETIC_ERROR", str(error))
    return _failure(current, "unknown", "FUEL_EXHAUSTED", "pure instruction budget exhausted")


def _validate_outcome(outcome: dict) -> dict:
    value = clone(outcome)
    if type(value) is not dict or type(value.get("status")) is not str:
        raise MachineStateError("effect outcome must be an envelope with status")
    if value["status"] == "returned":
        if set(value) != {"status", "value"}:
            raise MachineStateError("returned outcome requires exactly status and value")
    elif value["status"] == "raised":
        error = value.get("error")
        if (
            set(value) != {"status", "error"}
            or type(error) is not dict
            or set(error) != {"type", "message"}
            or any(type(item) is not str for item in error.values())
        ):
            raise MachineStateError("raised outcome requires error type and message")
    elif value["status"] == "interrupted_unknown":
        if set(value) - {"status", "error"}:
            raise MachineStateError("unexpected interrupted outcome fields")
    else:
        raise MachineStateError("unknown effect outcome status")
    return value


def resume(machine: Machine, outcome: dict) -> Machine:
    """Resume an effect with an actual or explicitly hypothetical envelope.

    The caller controls the source; mode is never upgraded. Uncertain effects
    stop as UNKNOWN so ordinary continuation code cannot hide an interruption.
    """
    current = Machine.from_dict(machine.to_dict())
    if current.status != "effect" or current.pending is None:
        raise MachineStateError("only an effect-suspended machine can be resumed")
    outcome = _validate_outcome(outcome)
    if outcome["status"] == "interrupted_unknown":
        _failure(
            current,
            "unknown",
            "INTERRUPTED_EFFECT",
            "effect outcome is uncertain; no retry",
        )
        return current
    frame = current.pending["frame"]
    _deliver(current, frame, outcome)
    return current


def snapshot_program(machine: Machine) -> tuple[dict, dict] | None:
    """Export a consumer checkpoint only when no omitted caller is required.

    A nonempty call stack, suspended effect, or middle-of-block position returns
    None. It would be unsound to turn an inner return into a root return.
    """
    current = Machine.from_dict(machine.to_dict())
    if (
        current.call_stack
        or current.status != "running"
        or current.op_index != 0
        or current.pending
    ):
        return None
    program = clone(current.program)
    program["entry"] = current.block_id
    return program, clone(current.registers)


__all__ = [
    "Boundary",
    "Machine",
    "new_machine",
    "resume",
    "run_until_boundary",
    "snapshot_program",
]
