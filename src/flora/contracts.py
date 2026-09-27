# SPDX-License-Identifier: Apache-2.0
"""Pure, consumer-relative contract checks and finite empirical guard synthesis.

PASS is a witness for a stated relation on one supplied, observed context. It is
never a task-success label or a proof about unseen inputs. No function in this
module invokes a tool, model, evaluator, or user-provided Python callback.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import ValidationError
from .opaque import OPAQUE_KEY, contains_opaque
from .values import canonical_json, clone, digest


class Verdict(str, Enum):
    """Truth of a specified local relation, with explicit unavailable evidence."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CheckResult:
    """A scoped, serializable witness; its verdict is not a correctness score."""

    verdict: Verdict
    relation: str
    witness: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "verdict", Verdict(self.verdict))
        except (TypeError, ValueError) as exc:
            raise ValidationError("invalid check verdict") from exc
        if not isinstance(self.relation, str) or not self.relation:
            raise ValidationError("check relation must be a nonempty string")
        if not isinstance(self.witness, dict):
            raise ValidationError("check witness must be a dictionary")
        object.__setattr__(self, "witness", clone(self.witness))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "relation": self.relation,
            "witness": clone(self.witness),
        }


_RELATIONS = {"DEFINED_PREFIX", "SAME_BOUNDARY", "SOURCE_FIDELITY"}


@dataclass
class ContextCheck:
    """An observed context against which a program or suspended consumer is checked.

    ``source_path`` addresses {inputs, receipts, memory}; ``output_path`` addresses
    a returned value, or the {tool,args} request at an effect frontier. Source modes
    are equal, substring, and member. ``inputs`` are the program's actual execution
    inputs and must exactly match its entry parameters when no checkpoint is used.
    Other source material can be supplied in ``memory`` or ``receipts``. All values
    are copied; arbitrary Python callbacks are not accepted.

    To check a consumer AFTER an actual effect, pass the resumed machine's complete
    serialization as ``reference_machine``. Its stack is preserved. An identical
    candidate program can use that checkpoint; a different program needs an explicit
    ``candidate_machine`` mapping. Missing mappings yield UNKNOWN, never entry replay.
    The caller, normally the runtime, is responsible for supplying actual evidence;
    the string ``mode='observed'`` is not a cryptographic provenance attestation.
    """

    id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    memory: dict[str, Any] = field(default_factory=dict)
    reference_program: dict[str, Any] | None = None
    relation: str = "DEFINED_PREFIX"
    source_path: list[str | int] | None = None
    output_path: list[str | int] = field(default_factory=list)
    source_mode: str = "equal"
    mode: str = "observed"
    reference_machine: dict[str, Any] | None = None
    candidate_machine: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValidationError("context id must be a nonempty string")
        if not isinstance(self.relation, str) or self.relation not in _RELATIONS:
            raise ValidationError(f"unknown local relation: {self.relation!r}")
        if not isinstance(self.mode, str) or self.mode not in {"observed", "hypothetical"}:
            raise ValidationError("context mode must be observed or hypothetical")
        if not isinstance(self.source_mode, str) or self.source_mode not in {
            "equal",
            "substring",
            "member",
        }:
            raise ValidationError("source_mode must be equal, substring, or member")
        if not isinstance(self.inputs, dict) or not isinstance(self.memory, dict):
            raise ValidationError("context inputs and memory must be dictionaries")
        if not isinstance(self.receipts, list) or any(
            not isinstance(r, dict) for r in self.receipts
        ):
            raise ValidationError("context receipts must be a list of dictionaries")
        for name in ("reference_program", "reference_machine", "candidate_machine"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"context {name} must be a dictionary or None")
        for name in (
            "inputs",
            "receipts",
            "memory",
            "reference_program",
            "reference_machine",
            "candidate_machine",
        ):
            setattr(self, name, clone(getattr(self, name)))
        if self.source_path is not None:
            _validate_path(self.source_path)
            self.source_path = list(self.source_path)
        _validate_path(self.output_path)
        self.output_path = list(self.output_path)

    def to_dict(self) -> dict[str, Any]:
        return clone({name: getattr(self, name) for name in self.__dataclass_fields__})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextCheck:
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValidationError("invalid ContextCheck fields")
        try:
            return cls(**clone(data))
        except TypeError as exc:
            raise ValidationError("invalid ContextCheck constructor fields") from exc


_MISSING = object()
_OPAQUE = object()
_TYPE_ALIASES = {
    "dict": "object",
    "object": "object",
    "list": "array",
    "array": "array",
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "float",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "null": "null",
    "NoneType": "null",
}
_ATOMIC = {"eq", "ne", "has", "type", "len_ge", "len_le", "lt", "le", "gt", "ge"}


def _validate_path(path: Any) -> None:
    if not isinstance(path, list) or len(path) > 32:
        raise ValidationError("predicate paths must be lists of at most 32 components")
    if any(not isinstance(p, str) and type(p) is not int for p in path):
        raise ValidationError("path components must be strings or integer indices")
    if any(type(p) is int and p < 0 for p in path):
        raise ValidationError("path indices must be nonnegative")


def _lookup(value: Any, path: list[str | int]) -> Any:
    for key in path:
        if isinstance(value, dict) and OPAQUE_KEY in value:
            return _OPAQUE
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            return _MISSING
    return value


def validate_predicate(predicate: dict[str, Any]) -> dict[str, Any]:
    """Validate and clone a bounded predicate DSL; malformed predicates are errors."""
    predicate = clone(predicate)
    count = 0

    def visit(node: Any, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 16 or count > 256 or not isinstance(node, dict):
            raise ValidationError("predicate exceeds bounds or is not a dictionary")
        op = node.get("op")
        if not isinstance(op, str):
            raise ValidationError("predicate op must be a string")
        if op in {"all", "any"}:
            if set(node) != {"op", "args"} or not isinstance(node["args"], list):
                raise ValidationError("logical predicates require only op and args")
            for child in node["args"]:
                visit(child, depth + 1)
        elif op == "not":
            if set(node) != {"op", "arg"}:
                raise ValidationError("not requires only op and arg")
            visit(node["arg"], depth + 1)
        elif op in _ATOMIC:
            required = {"op", "path"} if op == "has" else {"op", "path", "value"}
            allowed = {"op", "path", "value"}
            if not required <= set(node) or set(node) - allowed:
                raise ValidationError("invalid atomic predicate fields")
            _validate_path(node["path"])
            if op == "type" and (
                not isinstance(node["value"], str) or node["value"] not in _TYPE_ALIASES
            ):
                raise ValidationError("unsupported predicate type")
            if op in {"len_ge", "len_le"} and (type(node["value"]) is not int or node["value"] < 0):
                raise ValidationError("length thresholds must be nonnegative integers")
            if op == "has" and "value" in node:
                key = node["value"]
                if not isinstance(key, str) and not (type(key) is int and key >= 0):
                    raise ValidationError("has value must be a key or nonnegative index")
        else:
            raise ValidationError(f"unknown predicate op: {op!r}")

    visit(predicate, 0)
    return predicate


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    return {
        dict: "object",
        list: "array",
        str: "string",
        int: "integer",
        float: "float",
        bool: "boolean",
    }[type(value)]


def _evaluate(node: dict[str, Any], value: Any) -> bool | None:
    op = node["op"]
    if op in {"all", "any"}:
        children = [_evaluate(child, value) for child in node["args"]]
        if op == "all":
            return False if False in children else (None if None in children else True)
        return True if True in children else (None if None in children else False)
    if op == "not":
        result = _evaluate(node["arg"], value)
        return None if result is None else not result
    actual = _lookup(value, node["path"])
    if actual is _OPAQUE or (isinstance(actual, dict) and OPAQUE_KEY in actual):
        return None
    if op == "has":
        if actual is _MISSING:
            return False
        if "value" not in node:
            return True
        selected = _lookup(actual, [node["value"]])
        return None if selected is _OPAQUE else selected is not _MISSING
    if actual is _MISSING:
        return None
    expected = node["value"]
    if op in {"eq", "ne"}:
        if contains_opaque(actual) or contains_opaque(expected):
            return None
        equal = canonical_json(actual) == canonical_json(expected)
        return equal if op == "eq" else not equal
    if op == "type":
        typ = _TYPE_ALIASES[expected]
        return type(actual) in (int, float) if typ == "number" else _json_type(actual) == typ
    if op in {"len_ge", "len_le"}:
        if not isinstance(actual, (str, list, dict)):
            return None
        return len(actual) >= expected if op == "len_ge" else len(actual) <= expected
    comparable = (type(actual) in (int, float) and type(expected) in (int, float)) or (
        isinstance(actual, str) and isinstance(expected, str)
    )
    if not comparable:
        return None
    if op == "lt":
        return actual < expected
    if op == "le":
        return actual <= expected
    if op == "gt":
        return actual > expected
    return actual >= expected


def evaluate_predicate(predicate: dict[str, Any], value: Any) -> bool | None:
    """Evaluate a strict-JSON predicate using three-valued logic.

    Missing paths are UNKNOWN except ``has``, which reports actual absence. Equality
    is canonical JSON equality (True differs from 1, and 1 differs from 1.0).
    ``number`` excludes bool. VM arithmetic equality is a separate operation.
    Opaque object contents/metadata and equality involving opaque state are UNKNOWN.
    Empty all/any denote true/false. No Python expressions or callbacks are accepted.
    """
    return _evaluate(validate_predicate(predicate), clone(value))


def _boundary_value(boundary: Any) -> Any:
    if boundary.kind == "effect":
        return {"kind": "effect", "request": clone(boundary.request)}
    if boundary.kind in {"return", "replan"}:
        return {"kind": boundary.kind, "value": clone(boundary.value)}
    return {"kind": boundary.kind, "details": clone(boundary.details)}


def _machine_program(data: dict[str, Any]) -> Any:
    return data.get("program")


def _run(program: dict[str, Any], context: ContextCheck, *, reference: bool, fuel: int) -> Any:
    from .vm import Machine, new_machine, run_until_boundary

    checkpoint = context.reference_machine if reference else context.candidate_machine
    if checkpoint is None and not reference and context.reference_machine is not None:
        if canonical_json(_machine_program(context.reference_machine)) != canonical_json(program):
            return None
        checkpoint = context.reference_machine
    if checkpoint is not None:
        if checkpoint.get("mode", "observed") != "observed":
            return None
        if canonical_json(_machine_program(checkpoint)) != canonical_json(program):
            return None
        machine = Machine.from_dict(clone(checkpoint))
    else:
        machine = new_machine(program, clone(context.inputs), mode="observed")
    return run_until_boundary(
        machine, receipts=clone(context.receipts), memory=clone(context.memory), fuel=fuel
    )


def check_revision(
    candidate_program: dict[str, Any], context: ContextCheck, *, fuel: int = 50000
) -> CheckResult:
    """Run a candidate and its actual consumer prefix, stopping before every effect.

    SAME_BOUNDARY compares exact first requests, return values or replan payloads, not downstream
    success. Intentional behavior changes therefore fail that preservation relation;
    callers must record a different relation/version rather than reinterpret FAIL.
    No observed context is extended using an invented outcome.
    """
    if type(fuel) is not int or not 1 <= fuel <= 1000000:
        raise ValidationError("contract fuel must be between 1 and 1000000")
    context = ContextCheck.from_dict(context.to_dict())
    from .ir import parse_program

    witness = {"context_id": context.id, "scope": "this_input_and_first_pure_frontier_only"}

    def result(verdict: Verdict, reason: str, **more: Any) -> CheckResult:
        return CheckResult(verdict, context.relation, {**witness, "reason": reason, **more})

    if context.mode != "observed":
        return result(Verdict.UNKNOWN, "hypothetical_context_is_not_observed_evidence")
    try:
        program = parse_program(candidate_program)
        witness["program_digest"] = digest(program)
        candidate = _run(program, context, reference=False, fuel=fuel)
    except ValidationError as exc:
        return result(Verdict.UNKNOWN, "invalid_or_unavailable_program_context", error=str(exc))
    if candidate is None:
        return result(Verdict.UNKNOWN, "explicit_consumer_checkpoint_mapping_required")
    boundary = _boundary_value(candidate)
    if candidate.kind == "fault":
        if candidate.details.get("code") in {"VALUE_LIMIT", "STACK_LIMIT"}:
            return result(Verdict.UNKNOWN, "candidate_resource_limit", candidate=boundary)
        return result(Verdict.FAIL, "candidate_pure_prefix_fault", candidate=boundary)
    if candidate.kind not in {"effect", "return", "replan"}:
        return result(Verdict.UNKNOWN, "candidate_has_no_comparable_frontier", candidate=boundary)
    if context.relation == "DEFINED_PREFIX":
        return result(Verdict.PASS, "candidate_reached_defined_frontier", candidate=boundary)
    if context.relation == "SAME_BOUNDARY":
        reference_program = context.reference_program
        if reference_program is None and context.reference_machine is not None:
            reference_program = _machine_program(context.reference_machine)
        if reference_program is None:
            return result(Verdict.UNKNOWN, "reference_program_unavailable")
        try:
            reference = _run(parse_program(reference_program), context, reference=True, fuel=fuel)
        except ValidationError as exc:
            return result(Verdict.UNKNOWN, "reference_context_unavailable", error=str(exc))
        if reference is None or reference.kind not in {"effect", "return", "replan"}:
            return result(Verdict.UNKNOWN, "reference_has_no_comparable_frontier")
        old = _boundary_value(reference)
        if contains_opaque(boundary) or contains_opaque(old):
            return result(Verdict.UNKNOWN, "opaque_boundary_state_is_not_comparable")
        same = canonical_json(boundary) == canonical_json(old)
        return result(
            Verdict.PASS if same else Verdict.FAIL,
            "same_boundary" if same else "changed_boundary",
            candidate=boundary,
            reference=old,
        )
    if candidate.kind == "replan":
        return result(Verdict.UNKNOWN, "replan_has_no_source_comparable_output")
    if context.source_path is None:
        return result(Verdict.UNKNOWN, "source_path_unavailable")
    source = _lookup(
        {"inputs": context.inputs, "receipts": context.receipts, "memory": context.memory},
        context.source_path,
    )
    output = candidate.value if candidate.kind == "return" else candidate.request
    selected = _lookup(output, context.output_path)
    if source is _MISSING or selected is _MISSING:
        return result(Verdict.UNKNOWN, "source_or_output_path_missing")
    if (
        source is _OPAQUE
        or selected is _OPAQUE
        or contains_opaque(source)
        or contains_opaque(selected)
    ):
        return result(Verdict.UNKNOWN, "opaque_source_state_is_not_comparable")
    if context.source_mode == "equal":
        valid = canonical_json(selected) == canonical_json(source)
    elif context.source_mode == "substring":
        if not isinstance(source, str) or not isinstance(selected, str):
            return result(Verdict.UNKNOWN, "substring_requires_strings")
        valid = selected in source
    else:
        if not isinstance(source, list):
            return result(Verdict.UNKNOWN, "member_requires_source_list")
        valid = any(canonical_json(selected) == canonical_json(item) for item in source)
    return result(
        Verdict.PASS if valid else Verdict.FAIL,
        "source_match" if valid else "source_mismatch",
        source_path=context.source_path,
        output_path=context.output_path,
        source_digest=digest(source),
        output_digest=digest(selected),
    )


def _paths(value: Any, path: tuple = (), *, depth: int = 0):
    yield path
    if depth >= 4 or (isinstance(value, dict) and OPAQUE_KEY in value):
        return
    if isinstance(value, dict):
        for key in sorted(value)[:16]:
            yield from _paths(value[key], (*path, key), depth=depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:8]):
            yield from _paths(item, (*path, index), depth=depth + 1)


def fit_guard(
    samples: list[dict[str, Any]], *, max_atoms: int = 24, max_terms: int = 3, max_clauses: int = 4
) -> dict[str, Any]:
    """Fit a bounded empirical DNF guard; matching it never proves an unseen case.

    Terms are atoms per conjunction; clauses are disjuncts. Candidate atoms use
    observed types, existence, scalar values, and container lengths. UNKNOWN samples
    may not be admitted as positive evidence. A deterministic greedy cover chooses
    among the finite conjunctions; this is not claimed to be a global optimum.
    Partial coverage or indistinguishable contradictory samples is explicitly reported.
    """
    if not (
        type(max_atoms) is int
        and 1 <= max_atoms <= 32
        and type(max_terms) is int
        and 1 <= max_terms <= 3
        and type(max_clauses) is int
        and 1 <= max_clauses <= 8
    ):
        raise ValidationError("guard bounds: atoms 1..32, terms 1..3, clauses 1..8")
    if not isinstance(samples, list) or len(samples) > 256:
        raise ValidationError("guard fitting accepts at most 256 samples")
    samples = clone(samples)
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"value", "verdict"}:
            raise ValidationError("guard samples require exactly value and verdict")
        if not isinstance(sample["verdict"], str) or sample["verdict"] not in {
            v.value for v in Verdict
        }:
            raise ValidationError("invalid sample verdict")
    if len(canonical_json(samples).encode()) > 8 * 1024 * 1024:
        raise ValidationError("guard sample collection is too large")
    positives = {i for i, s in enumerate(samples) if s["verdict"] == "PASS"}
    forbidden = set(range(len(samples))) - positives
    paths = set()
    for sample in samples:
        for path in itertools.islice(_paths(sample["value"]), 128):
            paths.add(path)
    paths = sorted(paths, key=lambda p: (len(p), canonical_json(list(p))))[:64]
    atoms: dict[str, dict[str, Any]] = {}

    def add(atom: dict[str, Any]) -> None:
        atoms[canonical_json(atom)] = atom

    for path_tuple in paths:
        path = list(path_tuple)
        add({"op": "has", "path": path})
        observed = [_lookup(s["value"], path) for s in samples]
        observed = [
            v
            for v in observed
            if v is not _MISSING
            and v is not _OPAQUE
            and not (isinstance(v, dict) and OPAQUE_KEY in v)
        ]
        for typ in sorted({_json_type(v) for v in observed}):
            add({"op": "type", "path": path, "value": typ})
        constants = {canonical_json(v): v for v in observed if not isinstance(v, (list, dict))}
        for key in sorted(constants)[:8]:
            add({"op": "eq", "path": path, "value": constants[key]})
        for length in sorted({len(v) for v in observed if isinstance(v, (list, dict, str))})[:8]:
            add({"op": "len_ge", "path": path, "value": length})
            add({"op": "len_le", "path": path, "value": length})
    evaluated = []
    for key, atom in atoms.items():
        passed = {i for i, sample in enumerate(samples) if _evaluate(atom, sample["value"]) is True}
        if passed & positives:
            evaluated.append((atom, passed, key))
    evaluated.sort(key=lambda item: (len(item[1] & forbidden), -len(item[1] & positives), item[2]))
    evaluated = evaluated[:max_atoms]
    clauses = []
    examined = 0
    # The true guard is allowed only when all available samples are positive.
    if positives and not forbidden:
        clauses.append(({"op": "all", "args": []}, set(positives), 0))
    for length in range(1, max_terms + 1):
        for combo in itertools.combinations(evaluated, length):
            examined += 1
            passed = set.intersection(*(set(item[1]) for item in combo))
            covered = passed & positives
            if covered and not passed & forbidden:
                clauses.append(
                    ({"op": "all", "args": [item[0] for item in combo]}, covered, length)
                )
    selected = []
    uncovered = set(positives)
    for _ in range(max_clauses):
        useful = [item for item in clauses if item[1] & uncovered]
        if not useful:
            break
        chosen = min(
            useful, key=lambda item: (-len(item[1] & uncovered), item[2], canonical_json(item[0]))
        )
        selected.append(chosen[0])
        uncovered -= chosen[1]
        if not uncovered:
            break
    guard = {"op": "any", "args": selected}
    admitted = {i for i, s in enumerate(samples) if _evaluate(guard, s["value"]) is True}
    if admitted & forbidden:
        raise ValidationError("internal guard synthesis admitted a non-PASS sample")
    return {
        "guard": guard,
        "status": "covered"
        if positives and not uncovered
        else "partial"
        if admitted
        else "no_guard",
        "scope": "empirical_routing_only",
        "requires_runtime_check": True,
        "stats": {
            "samples": len(samples),
            "positive": len(positives),
            "negative": sum(s["verdict"] == "FAIL" for s in samples),
            "unknown": sum(s["verdict"] == "UNKNOWN" for s in samples),
            "covered_positive": len(admitted),
            "uncovered_positive": len(uncovered),
            "candidate_atoms": len(atoms),
            "retained_atoms": len(evaluated),
            "conjunctions_examined": examined,
            "feature_paths_considered": len(paths),
        },
    }


class ContractStore:
    """Bounded persistence of recheckable local witnesses, never hypothetical rewards.

    ``record(program, context, result=None, fuel=...)`` recomputes the check. If a
    supplied result disagrees, it is rejected, so a caller cannot append an invented
    PASS. JSON persistence is revalidated by ``from_dict`` using the saved fuel.
    Guard fitting uses context.inputs only and is indexed by exact program digest.
    """

    def __init__(self, *, max_records: int = 512, max_bytes: int = 1_048_576) -> None:
        from .resources import validate_limit

        if type(max_records) is not int or not 1 <= max_records <= 4096:
            raise ValidationError("max_records must be between 1 and 4096")
        self.max_records = max_records
        self.max_bytes = validate_limit(max_bytes, "contract max_bytes")
        self.dropped_records = 0
        self._records: list[dict[str, Any]] = []

    @property
    def records(self) -> list[dict[str, Any]]:
        return clone(self._records)

    def record(
        self,
        program: dict[str, Any],
        context: ContextCheck,
        result: CheckResult | None = None,
        *,
        fuel: int = 50000,
    ) -> CheckResult:
        checked = check_revision(program, context, fuel=fuel)
        if result is not None and canonical_json(result.to_dict()) != canonical_json(
            checked.to_dict()
        ):
            raise ValidationError("supplied check result does not match pure recomputation")
        record = {
            "program": clone(program),
            "program_digest": digest(program),
            "context": context.to_dict(),
            "result": checked.to_dict(),
            "fuel": fuel,
        }
        record["id"] = digest({"program": record["program"], "context": record["context"]})
        self._records = [old for old in self._records if old["id"] != record["id"]]
        self._records.append(record)
        self.trim_bytes(self.max_bytes)
        return checked

    def trim_bytes(self, max_bytes: int) -> dict[str, int]:
        """Evict complete oldest witnesses; checked results are never relabeled."""
        from .resources import retain_newest

        self._records, stats = retain_newest(
            self._records,
            max_bytes=max_bytes,
            max_records=self.max_records,
            resource="contract witnesses",
        )
        self.dropped_records += stats["dropped_records"]
        return stats

    def fit_guard(
        self,
        program: dict[str, Any],
        *,
        relation: str | None = None,
        max_atoms: int = 24,
        max_terms: int = 3,
        max_clauses: int = 4,
    ) -> dict[str, Any]:
        if relation is not None and (not isinstance(relation, str) or relation not in _RELATIONS):
            raise ValidationError("guard relation must name a supported local relation")
        key = digest(program)
        selected = [
            r
            for r in self._records
            if r["program_digest"] == key
            and (relation is None or r["context"]["relation"] == relation)
        ]
        # Never combine relations: a source check and an interface check are different labels.
        relations = {r["context"]["relation"] for r in selected}
        if len(relations) > 1:
            raise ValidationError("select one relation before fitting a guard")
        selected = selected[-256:]
        fitted = fit_guard(
            [
                {"value": r["context"]["inputs"], "verdict": r["result"]["verdict"]}
                for r in selected
            ],
            max_atoms=max_atoms,
            max_terms=max_terms,
            max_clauses=max_clauses,
        )
        return {
            **fitted,
            "program_digest": key,
            "relation": next(iter(relations), relation),
            "sample_ids": [r["id"] for r in selected],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "max_records": self.max_records,
            "max_bytes": self.max_bytes,
            "dropped_records": self.dropped_records,
            "records": self.records,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractStore:
        expected = {"version", "max_records", "dropped_records", "records"}
        if (
            not isinstance(data, dict)
            or set(data) not in (expected, expected | {"max_bytes"})
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValidationError("invalid contract store serialization")
        store = cls(max_records=data["max_records"], max_bytes=data.get("max_bytes", 1_048_576))
        if not isinstance(data["records"], list) or len(data["records"]) > store.max_records:
            raise ValidationError("invalid contract record count")
        if type(data["dropped_records"]) is not int or data["dropped_records"] < 0:
            raise ValidationError("invalid dropped record count")
        from .resources import encoded_size

        encoded_size(data["records"], limit=store.max_bytes, resource="contract witnesses")
        seen_ids = set()
        for item in data["records"]:
            if not isinstance(item, dict) or set(item) != {
                "program",
                "program_digest",
                "context",
                "result",
                "fuel",
                "id",
            }:
                raise ValidationError("invalid contract record fields")
            if digest(item["program"]) != item["program_digest"]:
                raise ValidationError("contract program digest mismatch")
            context = ContextCheck.from_dict(item["context"])
            result = item["result"]
            if not isinstance(result, dict) or set(result) != {"verdict", "relation", "witness"}:
                raise ValidationError("invalid stored check result")
            try:
                supplied = CheckResult(result["verdict"], result["relation"], result["witness"])
            except (TypeError, ValueError) as exc:
                raise ValidationError("invalid stored check result") from exc
            store.record(item["program"], context, supplied, fuel=item["fuel"])
            if store._records[-1]["id"] != item["id"]:
                raise ValidationError("contract record digest mismatch")
            if item["id"] in seen_ids:
                raise ValidationError("duplicate serialized contract record")
            seen_ids.add(item["id"])
        store.dropped_records = data["dropped_records"]
        return store
