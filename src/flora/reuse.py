# SPDX-License-Identifier: Apache-2.0
"""Mechanically checked reuse of consumer-scoped program/state variants.

An empirical guard is a routing hint only. Every hit reruns the pure migration
and the current local contract before replacing a machine. This module cannot
invoke host tools or add observations; it only returns an unexecuted machine.
The trusted caller registers variants after its historical revision checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import ContextCheck, Verdict, check_revision, evaluate_predicate, validate_predicate
from .errors import ValidationError
from .ir import parse_program
from .values import canonical_json, clone, digest
from .vm import Machine, new_machine, run_until_boundary

_FORMAT = "openharness-reuse-v1"
_MAX_STORE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ReuseDecision:
    accepted: bool
    machine: Machine | None
    report: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "machine": self.machine.to_dict() if self.machine is not None else None,
            "report": clone(self.report),
        }


def _scope(machine: Machine) -> dict:
    """Control identity, excluding dynamic captures checked on the actual input."""
    frames = [
        {
            "resume": frame["resume"],
            "bind": frame["bind"],
            "capture_names": sorted(frame["capture"]),
        }
        for frame in machine.call_stack
    ]
    return {
        "program_digest": digest(machine.program),
        "block_id": machine.block_id,
        "op_index": machine.op_index,
        "call_stack": frames,
        "continuation_digest": digest(frames),
    }


def _validate_scope(scope: dict, program: dict) -> dict:
    expected = {"program_digest", "block_id", "op_index", "call_stack", "continuation_digest"}
    if not isinstance(scope, dict) or set(scope) != expected:
        raise ValidationError("reuse scope has missing or unknown fields")
    scope = clone(scope)
    if scope["program_digest"] != digest(program):
        raise ValidationError("reuse scope source program digest mismatch")
    blocks = program["blocks"]
    block_id = scope["block_id"]
    if not isinstance(block_id, str) or block_id not in blocks:
        raise ValidationError("reuse scope block is unavailable")
    index = scope["op_index"]
    if type(index) is not int or not 0 <= index <= len(blocks[block_id]["ops"]):
        raise ValidationError("reuse scope operation index is invalid")
    frames = scope["call_stack"]
    if not isinstance(frames, list) or len(frames) > 128:
        raise ValidationError("reuse continuation stack is invalid")
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {"resume", "bind", "capture_names"}:
            raise ValidationError("reuse continuation frame has invalid fields")
        if (
            not isinstance(frame["resume"], str)
            or frame["resume"] not in blocks
            or not isinstance(frame["bind"], str)
        ):
            raise ValidationError("reuse continuation target is invalid")
        names = frame["capture_names"]
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValidationError("reuse continuation capture names must be strings")
        if names != sorted(set(names)) or frame["bind"] in names:
            raise ValidationError("reuse continuation capture names must be unique and sorted")
        if set(blocks[frame["resume"]]["params"]) != set(names) | {frame["bind"]}:
            raise ValidationError("reuse continuation bindings do not match target")
    if scope["continuation_digest"] != digest(frames):
        raise ValidationError("reuse continuation digest mismatch")
    return scope


def _record(value: dict) -> dict:
    required = {"id", "mode", "source_program", "scope", "program", "migration", "guard"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValidationError("reuse variant has missing or unknown fields")
    result = clone(value)
    if not isinstance(result["id"], str) or not result["id"] or len(result["id"]) > 256:
        raise ValidationError(
            "reuse variant ID must be a nonempty string of at most 256 characters"
        )
    if not isinstance(result["mode"], str) or result["mode"] not in {"PRESERVE", "EXTEND"}:
        raise ValidationError("only PRESERVE and EXTEND variants may be automatically reused")
    result["source_program"] = parse_program(result["source_program"])
    result["scope"] = _validate_scope(result["scope"], result["source_program"])
    result["program"] = parse_program(result["program"])
    result["migration"] = parse_program(result["migration"], allowed_tools=[])
    migration = result["migration"]
    if migration["blocks"][migration["entry"]]["params"] != ["context"]:
        raise ValidationError("reuse migration must have exactly the context entry parameter")
    result["guard"] = validate_predicate(result["guard"])
    return result


def _same_machine(first: Machine, second: Machine) -> bool:
    a, b = first.to_dict(), second.to_dict()
    # Resetting a bookkeeping step counter is not a useful state revision.
    a.pop("steps")
    b.pop("steps")
    return canonical_json(a) == canonical_json(b)


class ReuseLibrary:
    """At most 64 validated variants, in deterministic oldest-first order.

    Registration does not itself establish historical correctness; the runtime
    must register only accepted revisions. This is not a trust-bearing cache:
    restored or manually registered guards still cannot bypass current checks.
    Captured continuation VALUES may vary within one scope, but structure must
    match and the full current stack participates in the relation check.
    ``fuel`` in route is a bound for each pure prefix; variant count and stored
    bytes are separately bounded. No external action occurs during routing.
    """

    def __init__(self, max_variants: int = 64) -> None:
        if type(max_variants) is not int or not 1 <= max_variants <= 64:
            raise ValidationError("max_variants must be between 1 and 64")
        self.max_variants = max_variants
        self.dropped_variants = 0
        self._variants: list[dict] = []

    @property
    def variants(self) -> list[dict]:
        return clone(self._variants)

    def register(
        self,
        source_machine: Machine,
        program: dict,
        migration: dict,
        guard: dict,
        *,
        mode: str = "PRESERVE",
        variant_id: str | None = None,
    ) -> str:
        if not isinstance(source_machine, Machine):
            raise ValidationError("reuse registration needs a source Machine")
        source = Machine.from_dict(source_machine.to_dict())
        if source.mode != "observed":
            raise ValidationError(
                "hypothetical or replay machines cannot register observed reuse variants"
            )
        material = {
            "source_program": source.program,
            "scope": _scope(source),
            "program": program,
            "migration": migration,
            "guard": guard,
            "mode": mode,
        }
        ident = variant_id if variant_id is not None else "variant_" + digest(material)[:24]
        item = _record({"id": ident, **material})
        existing = next((v for v in self._variants if v["id"] == ident), None)
        if existing is not None:
            if canonical_json(existing) == canonical_json(item):
                return ident
            raise ValidationError("reuse variant ID already refers to a different proposal")
        proposed = [*self._variants, item]
        dropped = max(0, len(proposed) - self.max_variants)
        proposed = proposed[dropped:]
        while True:
            serialized = {
                "format": _FORMAT,
                "max_variants": self.max_variants,
                "dropped_variants": self.dropped_variants + dropped,
                "variants": proposed,
            }
            if len(canonical_json(serialized).encode("utf-8")) <= _MAX_STORE_BYTES:
                break
            if len(proposed) == 1:
                raise ValidationError("reuse variant exceeds the 1 MiB serialized library limit")
            proposed.pop(0)
            dropped += 1
        self._variants = proposed
        self.dropped_variants += dropped
        return ident

    def drop_oldest(self) -> bool:
        """Evict one optional cache entry, e.g. to fit a durable checkpoint."""
        if not self._variants:
            return False
        self._variants.pop(0)
        self.dropped_variants += 1
        return True

    def route(
        self, machine: Machine, *, receipts: list[dict], memory: dict, fuel: int = 50000
    ) -> ReuseDecision:
        if not isinstance(machine, Machine):
            raise ValidationError("reuse routing needs a Machine")
        if type(fuel) is not int or not 1 <= fuel <= 1000000:
            raise ValidationError("reuse fuel must be between 1 and 1000000")
        if (
            not isinstance(receipts, list)
            or any(not isinstance(r, dict) for r in receipts)
            or not isinstance(memory, dict)
        ):
            raise ValidationError("reuse requires actual receipt objects and memory")
        current = Machine.from_dict(machine.to_dict())
        actual_receipts, actual_memory = clone(receipts), clone(memory)
        report: dict[str, Any] = {
            "scope": _scope(current),
            "attempts": [],
            "reason": "no_matching_scope",
            "proof_scope": "current_input_and_first_pure_frontier_only",
            "task_correctness": "UNVERIFIED",
        }
        if current.mode != "observed":
            report["reason"] = "non_observed_machine"
            return ReuseDecision(False, None, report)
        signature = canonical_json(report["scope"])
        matches = [v for v in self._variants if canonical_json(v["scope"]) == signature]
        if matches:
            report["reason"] = "no_variant_passed_current_check"
        for item in matches:
            attempt: dict[str, Any] = {"variant_id": item["id"], "mode": item["mode"]}
            report["attempts"].append(attempt)
            guard_value = evaluate_predicate(item["guard"], current.registers)
            attempt["guard"] = guard_value
            if guard_value is not True:
                attempt["reason"] = "guard_false" if guard_value is False else "guard_unknown"
                continue
            material = {
                "inputs": clone(current.registers),
                "receipts": actual_receipts,
                "memory": actual_memory,
            }
            migration = run_until_boundary(
                new_machine(item["migration"], {"context": material}),
                receipts=actual_receipts,
                memory=actual_memory,
                fuel=fuel,
            )
            if migration.kind != "return" or not isinstance(migration.value, dict):
                attempt.update(
                    verdict="UNKNOWN",
                    reason="migration_did_not_return_input_object",
                    migration_boundary={
                        "kind": migration.kind,
                        "details": clone(migration.details),
                    },
                )
                continue
            try:
                candidate = new_machine(item["program"], migration.value, mode="observed")
            except ValidationError as exc:
                attempt.update(
                    verdict="UNKNOWN", reason="migration_inputs_invalid", details=str(exc)
                )
                continue
            context = ContextCheck(
                id=f"reuse:{item['id']}",
                inputs=current.registers,
                receipts=actual_receipts,
                memory=actual_memory,
                reference_program=current.program,
                reference_machine=current.to_dict(),
                candidate_machine=candidate.to_dict(),
                relation="SAME_BOUNDARY",
            )
            if item["mode"] == "EXTEND":
                previous = ContextCheck(
                    id=f"reuse:{item['id']}:old_defined",
                    inputs=current.registers,
                    receipts=actual_receipts,
                    memory=actual_memory,
                    reference_program=current.program,
                    reference_machine=current.to_dict(),
                    relation="DEFINED_PREFIX",
                )
                old_check = check_revision(current.program, previous, fuel=fuel)
                attempt["old_defined"] = old_check.to_dict()
                if old_check.verdict == Verdict.FAIL:
                    context.relation = "DEFINED_PREFIX"
            check = check_revision(item["program"], context, fuel=fuel)
            attempt["check"] = check.to_dict()
            attempt["verdict"] = check.verdict.value
            if check.verdict != Verdict.PASS:
                attempt["reason"] = "current_relation_not_passed"
                continue
            if _same_machine(current, candidate):
                attempt["reason"] = "no_state_change"
                continue
            attempt["reason"] = "current_relation_passed"
            report.update(reason="accepted", variant_id=item["id"], relation=context.relation)
            return ReuseDecision(True, candidate, clone(report))
        return ReuseDecision(False, None, clone(report))

    def to_dict(self) -> dict:
        return clone(
            {
                "format": _FORMAT,
                "max_variants": self.max_variants,
                "dropped_variants": self.dropped_variants,
                "variants": self._variants,
            }
        )

    @classmethod
    def from_dict(cls, data: dict) -> ReuseLibrary:
        required = {"format", "max_variants", "dropped_variants", "variants"}
        if not isinstance(data, dict) or set(data) != required or data["format"] != _FORMAT:
            raise ValidationError("invalid serialized reuse library")
        if len(canonical_json(data).encode("utf-8")) > _MAX_STORE_BYTES:
            raise ValidationError("serialized reuse library exceeds 1 MiB")
        data = clone(data)
        obj = cls(data["max_variants"])
        if type(data["dropped_variants"]) is not int or data["dropped_variants"] < 0:
            raise ValidationError("invalid reuse eviction count")
        if not isinstance(data["variants"], list) or len(data["variants"]) > obj.max_variants:
            raise ValidationError("serialized reuse variant count exceeds limit")
        items = [_record(item) for item in data["variants"]]
        if len({item["id"] for item in items}) != len(items):
            raise ValidationError("serialized reuse variant IDs must be unique")
        obj._variants = items
        obj.dropped_variants = data["dropped_variants"]
        return obj
