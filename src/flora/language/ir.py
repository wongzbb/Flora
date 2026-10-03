# SPDX-License-Identifier: Apache-2.0
"""Validation for the finite, explicit-continuation Flora IR."""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any

from flora.support.errors import ValidationError
from flora.support.values import clone

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
OP_ARITIES = {
    "const": (1, 1),
    "get": (2, 2),
    "get_default": (3, 3),
    "has": (2, 2),
    "set": (3, 3),
    "delete": (2, 2),
    "keys": (1, 1),
    "values": (1, 1),
    "length": (1, 1),
    "append": (2, 2),
    "extend": (2, 2),
    "slice": (2, 4),
    "add": (2, 2),
    "sub": (2, 2),
    "mul": (2, 2),
    "div": (2, 2),
    "mod": (2, 2),
    "eq": (2, 2),
    "ne": (2, 2),
    "lt": (2, 2),
    "le": (2, 2),
    "gt": (2, 2),
    "ge": (2, 2),
    "and": (2, 128),
    "or": (2, 128),
    "not": (1, 1),
    "concat": (2, 128),
    "contains": (2, 2),
    "to_string": (1, 1),
    "parse_json": (1, 1),
    "read_receipt": (1, 1),
    "read_memory": (0, 2),
    "assert": (1, 2),
    "type": (1, 1),
}


def _error(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def _keys(value: Any, required: set[str], path: str) -> None:
    if type(value) is not dict:
        _error(path, "expected an object")
    if set(value) != required:
        _error(path, f"expected fields {sorted(required)!r}; got {sorted(value)!r}")


def _name(value: Any, path: str) -> None:
    if type(value) is not str or NAME.fullmatch(value) is None:
        _error(path, "expected an identifier of at most 128 ASCII characters")


def _expression(value: Any, scope: set[str], path: str) -> None:
    if type(value) is dict:
        if "var" in value:
            _keys(value, {"var"}, path)
            _name(value["var"], path)
            if value["var"] not in scope:
                _error(path, f"undefined variable {value['var']!r}")
        elif "literal" in value:
            _keys(value, {"literal"}, path)
            # clone at parse entry already checked the escaped JSON value.
        else:
            for key, item in value.items():
                _expression(item, scope, f"{path}.{key}")
    elif type(value) is list:
        for index, item in enumerate(value):
            _expression(item, scope, f"{path}[{index}]")


def parse_program(data: dict, allowed_tools: Collection[str] | None = None) -> dict:
    """Validate every block, reference and explicit continuation, then clone.

    ``allowed_tools=None`` skips capability membership checks, not tool-name or
    expression checks. Runtime adapters remain responsible for actual tools.
    Static checking does not prove termination, tool semantics, or task success.
    """
    program = clone(data)
    _keys(program, {"version", "entry", "blocks"}, "program")
    if type(program["version"]) is not int or program["version"] != 1:
        _error("version", "only version 1 is supported")
    blocks = program["blocks"]
    if type(blocks) is not dict or not 1 <= len(blocks) <= 512:
        _error("blocks", "expected between 1 and 512 blocks")
    params = {}
    total_ops = 0
    for block_id, block in blocks.items():
        _name(block_id, "block id")
        _keys(block, {"params", "ops", "term"}, f"blocks.{block_id}")
        if type(block["params"]) is not list or len(block["params"]) > 128:
            _error(block_id, "params must be a list of at most 128 identifiers")
        for param in block["params"]:
            _name(param, f"{block_id}.params")
        if len(set(block["params"])) != len(block["params"]):
            _error(block_id, "duplicate block parameter")
        params[block_id] = set(block["params"])
        if type(block["ops"]) is not list:
            _error(block_id, "ops must be a list")
        total_ops += len(block["ops"])
    if total_ops > 10_000:
        _error("blocks", "program exceeds 10000 operations")
    if type(program["entry"]) is not str or program["entry"] not in blocks:
        _error("entry", "unknown entry block")
    if allowed_tools is not None and (
        isinstance(allowed_tools, (str, bytes))
        or any(type(tool) is not str for tool in allowed_tools)
    ):
        _error("allowed_tools", "expected a collection of tool names")

    def arguments(mapping: Any, target: Any, scope: set[str], path: str) -> None:
        if type(target) is not str or target not in blocks:
            _error(path, f"unknown target {target!r}")
        if type(mapping) is not dict or set(mapping) != params[target]:
            actual = sorted(mapping) if type(mapping) is dict else type(mapping).__name__
            _error(
                path,
                f"argument keys must match {target!r} parameters; "
                f"expected {sorted(params[target])!r}, got {actual!r}",
            )
        for key, value in mapping.items():
            _expression(value, scope, f"{path}.{key}")

    def continuation(term: dict, scope: set[str], path: str) -> None:
        _name(term["bind"], f"{path}.bind")
        capture = term["capture"]
        if type(capture) is not dict or term["bind"] in capture:
            _error(path, "capture must be an object without the bind name")
        for name, expr in capture.items():
            _name(name, f"{path}.capture")
            _expression(expr, scope, f"{path}.capture.{name}")
        resume = term["resume"]
        if type(resume) is not str or resume not in blocks:
            _error(path, "unknown resume block")
        if params[resume] != set(capture) | {term["bind"]}:
            _error(path, "resume parameters must equal capture keys plus bind")

    for block_id, block in blocks.items():
        scope = set(params[block_id])
        for index, operation in enumerate(block["ops"]):
            path = f"{block_id}.ops[{index}]"
            _keys(operation, {"op", "dest", "args"}, path)
            op = operation["op"]
            if type(op) is not str or op not in OP_ARITIES:
                _error(path, f"unknown pure operation {op!r}")
            _name(operation["dest"], f"{path}.dest")
            if operation["dest"] in scope:
                _error(path, f"duplicate SSA binding {operation['dest']!r}")
            args = operation["args"]
            minimum, maximum = OP_ARITIES[op]
            if type(args) is not list or not minimum <= len(args) <= maximum:
                _error(path, f"{op} expects {minimum}..{maximum} arguments")
            for arg_index, arg in enumerate(args):
                _expression(arg, scope, f"{path}.args[{arg_index}]")
            scope.add(operation["dest"])
        term = block["term"]
        path = f"{block_id}.term"
        if type(term) is not dict or type(term.get("op")) is not str:
            _error(path, "expected a terminator object with an op")
        op = term["op"]
        if op == "return":
            _keys(term, {"op", "value"}, path)
            _expression(term["value"], scope, f"{path}.value")
        elif op == "replan":
            _keys(term, {"op", "reason", "state"}, path)
            _expression(term["reason"], scope, f"{path}.reason")
            _expression(term["state"], scope, f"{path}.state")
        elif op == "jump":
            _keys(term, {"op", "target", "args"}, path)
            arguments(term["args"], term["target"], scope, path)
        elif op == "branch":
            _keys(term, {"op", "condition", "yes", "no", "args"}, path)
            _expression(term["condition"], scope, f"{path}.condition")
            arguments(term["args"], term["yes"], scope, path)
            arguments(term["args"], term["no"], scope, path)
        elif op == "call":
            _keys(term, {"op", "target", "args", "resume", "bind", "capture"}, path)
            arguments(term["args"], term["target"], scope, path)
            continuation(term, scope, path)
        elif op == "effect":
            _keys(term, {"op", "tool", "args", "resume", "bind", "capture"}, path)
            if type(term["tool"]) is not str or not term["tool"].strip():
                _error(path, "tool must be a nonempty name")
            if allowed_tools is not None and term["tool"] not in allowed_tools:
                _error(path, f"tool not allowed: {term['tool']!r}")
            _expression(term["args"], scope, f"{path}.args")
            continuation(term, scope, path)
        elif op == "alternative":
            _keys(term, {"op", "branches", "args", "resume", "bind", "capture"}, path)
            branches = term["branches"]
            if type(branches) is not list or not 1 <= len(branches) <= 16:
                _error(path, "alternative requires 1..16 branch blocks")
            if any(type(target) is not str for target in branches):
                _error(path, "alternative targets must be block identifiers")
            if len(set(branches)) != len(branches):
                _error(path, "duplicate alternative branch")
            for target in branches:
                arguments(term["args"], target, scope, path)
            continuation(term, scope, path)
        else:
            _error(path, f"unknown terminator {op!r}")
    return program


__all__ = ["OP_ARITIES", "parse_program"]
