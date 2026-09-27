"""Conservative, bounded checks of model-authored diagnostic hypotheses.

A score describes discrimination in supplied hypothetical examples. It is neither
expected information gain nor a probability of task success. Hypothetical results
are never real observations and are never evidence for a contract PASS.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from flora.checks.contracts import evaluate_predicate
from flora.language.ir import parse_program
from flora.language.vm import Machine, new_machine, resume, run_until_boundary
from flora.support.errors import StaleAnchor, ValidationError
from flora.support.values import canonical_json, clone, digest

_ATOMS = {"eq", "ne", "has", "type", "len_ge", "len_le", "lt", "le", "gt", "ge"}
_TYPES = {
    "dict": {"dict"},
    "object": {"dict"},
    "list": {"list"},
    "array": {"list"},
    "str": {"str"},
    "string": {"str"},
    "int": {"int"},
    "integer": {"int"},
    "float": {"float"},
    "number": {"int", "float"},
    "bool": {"bool"},
    "boolean": {"bool"},
    "null": {"null"},
}
_TYPE_CANON = {
    "dict": "object",
    "list": "array",
    "str": "string",
    "int": "integer",
    "bool": "boolean",
}


def validate_predicate(predicate: dict) -> dict:
    """Validate the shared predicate DSL, reject extra fields, and return JSON copy."""
    nodes = 0

    def visit(node: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 128:
            raise ValidationError("predicate complexity limit exceeded")
        if not isinstance(node, dict) or not isinstance(node.get("op"), str):
            raise ValidationError("predicate must be an object with an op")
        op = node["op"]
        if op in {"all", "any"}:
            if (
                set(node) != {"op", "args"}
                or not isinstance(node["args"], list)
                or not node["args"]
            ):
                raise ValidationError("all/any require a nonempty args list and no extra fields")
            for child in node["args"]:
                visit(child, depth + 1)
        elif op == "not":
            if set(node) != {"op", "arg"}:
                raise ValidationError("not requires exactly one arg")
            visit(node["arg"], depth + 1)
        elif op in _ATOMS:
            required = {"op", "path"} if op == "has" else {"op", "path", "value"}
            allowed = {"op", "path", "value"}
            if not required <= set(node) or not set(node) <= allowed:
                raise ValidationError("predicate has missing or unknown fields")
            path = node["path"]
            if (
                not isinstance(path, list)
                or len(path) > 32
                or any(
                    not isinstance(p, (str, int))
                    or isinstance(p, bool)
                    or (isinstance(p, int) and p < 0)
                    for p in path
                )
            ):
                raise ValidationError(
                    "predicate path must contain strings/nonnegative integer indices"
                )
            if op == "type" and (not isinstance(node["value"], str) or node["value"] not in _TYPES):
                raise ValidationError("unknown predicate type name")
            if op in {"len_ge", "len_le"} and (
                isinstance(node["value"], bool)
                or not isinstance(node["value"], int)
                or node["value"] < 0
            ):
                raise ValidationError("length bound must be a nonnegative integer")
            if "value" in node:
                canonical_json(node["value"])
        else:
            raise ValidationError(f"unknown predicate operator: {op}")

    visit(predicate)
    if len(canonical_json(predicate).encode("utf-8")) > 65536:
        raise ValidationError("predicate exceeds 64 KiB")
    return clone(predicate)


def _normalized(predicate: dict) -> dict:
    p = clone(predicate)
    if p["op"] in {"all", "any"}:
        children = {}
        for child in p["args"]:
            normalized = _normalized(child)
            nested = normalized["args"] if normalized["op"] == p["op"] else [normalized]
            for part in nested:
                children[_signature(part)] = part
        p["args"] = [children[key] for key in sorted(children)]
        if len(p["args"]) == 1:
            return p["args"][0]
    elif p["op"] == "not":
        p["arg"] = _normalized(p["arg"])
        if p["arg"]["op"] == "not":
            return p["arg"]["arg"]
    elif p["op"] == "type":
        p["value"] = _TYPE_CANON.get(p["value"], p["value"])
    return p


def _signature(value: Any) -> str:
    return canonical_json(value)


def _exclusive(p: dict, q: dict) -> bool:
    # Sufficient rules only. Failure to prove exclusion is not proof of overlap.
    if p["op"] == "not" and _signature(p["arg"]) == _signature(q):
        return True
    if q["op"] == "not" and _signature(q["arg"]) == _signature(p):
        return True
    if p["op"] == "any":
        return all(_exclusive(c, q) for c in p["args"])
    if q["op"] == "any":
        return all(_exclusive(p, c) for c in q["args"])
    if p["op"] == "all":
        return any(_exclusive(c, q) for c in p["args"])
    if q["op"] == "all":
        return any(_exclusive(p, c) for c in q["args"])
    if p.get("path") != q.get("path"):
        return False
    if p["op"] == q["op"] == "eq":
        return _signature(p["value"]) != _signature(q["value"])
    if {p["op"], q["op"]} == {"eq", "ne"}:
        return _signature(p["value"]) == _signature(q["value"])
    if p["op"] == q["op"] == "type":
        return not (_TYPES[p["value"]] & _TYPES[q["value"]])
    return False


def proven_exclusive(first: dict, second: dict) -> bool:
    """Conservatively prove two predicates cannot both be True (three-valued DSL)."""
    return _exclusive(
        _normalized(validate_predicate(first)), _normalized(validate_predicate(second))
    )


def validate_diagnostic(
    diag: dict,
    *,
    allowed_tools: Collection[str] | None = None,
    candidate_ids: Collection[str] | None = None,
) -> dict:
    """Validate exact compiler interchange; does not run a tool or infer truth."""
    if not isinstance(diag, dict) or set(diag) != {
        "id",
        "program",
        "inputs",
        "forecasts",
        "witnesses",
    }:
        raise ValidationError("diagnostic has missing or unknown fields")
    if not isinstance(diag["id"], str) or not diag["id"] or len(diag["id"]) > 128:
        raise ValidationError("diagnostic id must be a nonempty string of at most 128 characters")
    program = parse_program(diag["program"], allowed_tools=allowed_tools)
    if not isinstance(diag["inputs"], dict):
        raise ValidationError("diagnostic inputs must be an object")
    canonical_json(diag["inputs"])
    new_machine(program, diag["inputs"], mode="hypothetical")
    forecasts = diag["forecasts"]
    if not isinstance(forecasts, list) or len(forecasts) > 3:
        raise ValidationError("diagnostic permits at most three forecasts")
    known = set(candidate_ids) if candidate_ids is not None else None
    seen: set[str] = set()
    for forecast in forecasts:
        if not isinstance(forecast, dict) or set(forecast) != {"candidate_id", "predicate"}:
            raise ValidationError("forecast requires exactly candidate_id and predicate")
        ident = forecast["candidate_id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValidationError("forecast candidate ids must be unique nonempty strings")
        if known is not None and ident not in known:
            raise ValidationError(f"forecast names unknown candidate: {ident}")
        seen.add(ident)
        validate_predicate(forecast["predicate"])
    witnesses = diag["witnesses"]
    if not isinstance(witnesses, list) or len(witnesses) > 8:
        raise ValidationError("diagnostic permits at most eight hypothetical witnesses")
    if len(canonical_json(diag).encode("utf-8")) > 1048576:
        raise ValidationError("diagnostic exceeds 1 MiB")
    result = clone(diag)
    result["program"] = program
    return result


@dataclass(frozen=True)
class DiagnosticReport:
    id: str
    request: dict | None
    score: float
    anchor_epoch: int
    anchor_digest: str
    groups: list[dict]
    witness_results: list[dict]
    pairs: list[dict]
    reasons: list[str]

    @property
    def eligible(self) -> bool:
        return self.score > 0

    def to_dict(self) -> dict:
        return clone(
            {
                "id": self.id,
                "request": self.request,
                "score": self.score,
                "anchor_epoch": self.anchor_epoch,
                "anchor_digest": self.anchor_digest,
                "eligible": self.eligible,
                "groups": self.groups,
                "witness_results": self.witness_results,
                "pairs": self.pairs,
                "reasons": self.reasons,
                "hypothetical": True,
                "contract_evidence": False,
                "score_meaning": "structural discrimination on supplied hypothetical examples; not probability or task value",
            }
        )


def _boundary_summary(boundary) -> dict:
    result = {"kind": boundary.kind, "mode": "hypothetical"}
    if boundary.kind == "effect":
        result["request"] = clone(boundary.request)
    elif boundary.kind in {"return", "replan"}:
        result["value"] = clone(boundary.value)
    elif boundary.kind in {"fault", "unknown"}:
        result["details"] = clone(boundary.details)
    return result


def _relevant_difference(a: dict, b: dict) -> bool:
    meaningful = {"effect", "return"}
    if a["kind"] not in meaningful | {"fault"} or b["kind"] not in meaningful | {"fault"}:
        return False
    if a["kind"] == b["kind"] == "fault":
        return False
    return _signature(a) != _signature(b)


def evaluate_diagnostic(
    diag: dict,
    effect_boundary=None,
    *,
    candidate_ids: Collection[str] | None = None,
    receipts: Sequence[dict] = (),
    memory: dict | None = None,
    anchor_epoch: int = 0,
    anchor_digest: str = "",
    fuel: int = 10000,
) -> DiagnosticReport:
    """Check predictions and hypothetical pure continuation behavior, never tools.

    A diagnostic must first pause at an effect. If an observed effect Boundary is
    provided, its request must equal the diagnostic's first request. Actual prefix
    anchoring is carried in the report and checked again when feedback is consumed.
    Internal witness execution always uses ``mode='hypothetical'`` and stops before
    the next effect. Caller-provided witness values remain model-authored examples.
    """
    checked = validate_diagnostic(diag, candidate_ids=candidate_ids)
    if (
        isinstance(anchor_epoch, bool)
        or not isinstance(anchor_epoch, int)
        or anchor_epoch < 0
        or not isinstance(anchor_digest, str)
    ):
        raise ValidationError("invalid diagnostic anchor")
    if isinstance(fuel, bool) or not isinstance(fuel, int) or not 0 < fuel <= 50000:
        raise ValidationError("diagnostic fuel must be in 1..50000")
    source = run_until_boundary(
        new_machine(checked["program"], checked["inputs"], mode="hypothetical"),
        receipts=receipts,
        memory=memory,
        fuel=fuel,
    )
    reasons: list[str] = []
    request = clone(source.request) if source.kind == "effect" else None
    if effect_boundary is not None and (
        effect_boundary.kind != "effect"
        or source.kind != "effect"
        or _signature(effect_boundary.request) != _signature(source.request)
    ):
        raise ValidationError("provided boundary is not this diagnostic's first effect request")
    if source.kind != "effect":
        return DiagnosticReport(
            checked["id"],
            None,
            0.0,
            anchor_epoch,
            anchor_digest,
            [],
            [],
            [],
            ["diagnostic did not first reach an effect"],
        )

    grouped: dict[str, dict] = {}
    for forecast in checked["forecasts"]:
        pred = _normalized(forecast["predicate"])
        sig = _signature(pred)
        group = grouped.setdefault(sig, {"candidate_ids": [], "predicate": pred})
        group["candidate_ids"].append(forecast["candidate_id"])
    groups = [grouped[key] for key in sorted(grouped)]
    for group in groups:
        group["candidate_ids"].sort()

    witnesses = {_signature(w): w for w in checked["witnesses"]}
    witness_results: list[dict] = []
    for key in sorted(witnesses):
        witness = witnesses[key]
        machine_data = source.machine.to_dict()
        machine_data["mode"] = "hypothetical"
        hypothetical = Machine.from_dict(machine_data)
        continued = resume(hypothetical, {"status": "returned", "value": clone(witness)})
        boundary = run_until_boundary(continued, receipts=receipts, memory=memory, fuel=fuel)
        witness_results.append(
            {
                "witness": clone(witness),
                "witness_digest": digest(witness),
                "boundary": _boundary_summary(boundary),
                "predicate_results": [evaluate_predicate(g["predicate"], witness) for g in groups],
                "hypothetical": True,
                "contract_evidence": False,
            }
        )
    pairs: list[dict] = []
    distinguished = 0
    for i, j in combinations(range(len(groups)), 2):
        exclusive = _exclusive(groups[i]["predicate"], groups[j]["predicate"])
        examples = None
        if exclusive:
            for left in witness_results:
                if left["predicate_results"][i] is not True:
                    continue
                for right in witness_results:
                    if right["predicate_results"][j] is True and _relevant_difference(
                        left["boundary"], right["boundary"]
                    ):
                        examples = [left["witness_digest"], right["witness_digest"]]
                        break
                if examples is not None:
                    break
        useful = examples is not None
        distinguished += int(useful)
        pairs.append(
            {
                "groups": [i, j],
                "exclusive_proven": exclusive,
                "distinct_continuations": useful,
                "supporting_hypothetical_witnesses": examples,
            }
        )
    if not pairs:
        reasons.append("fewer than two distinct forecast signatures")
    elif not distinguished:
        reasons.append("no exclusive forecast pair with demonstrated distinct continuations")
    if not witness_results:
        reasons.append("no hypothetical witness values supplied")
    return DiagnosticReport(
        checked["id"],
        request,
        distinguished / max(1, len(pairs)),
        anchor_epoch,
        anchor_digest,
        groups,
        witness_results,
        pairs,
        reasons,
    )


def check_forecasts(
    diag: dict,
    actual_event: dict,
    *,
    expected_request: dict,
    expected_event_id: int,
    expected_previous_hash: str,
) -> dict:
    """Check one authenticated-by-caller real event against local predictions.

    Exact request, event id and prefix hash are mandatory: matching tool arguments
    alone cannot identify repeated stateful calls. This function does not validate
    the journal's cryptographic chain; the caller must obtain the record from its
    trusted trace store. PASS means only that this local forecast matched this value.
    """
    checked = validate_diagnostic(diag)
    if not isinstance(actual_event, dict):
        raise ValidationError("actual event must be an object")
    if actual_event.get("hypothetical") is True or actual_event.get("mode") == "hypothetical":
        raise ValidationError("hypothetical outcomes cannot be real forecast feedback")
    actual_id = actual_event.get("event_id")
    if isinstance(actual_id, bool) or not isinstance(actual_id, int) or actual_id < 0:
        raise ValidationError("actual event id must be a nonnegative integer")
    if (
        isinstance(expected_event_id, bool)
        or not isinstance(expected_event_id, int)
        or expected_event_id < 0
    ):
        raise ValidationError("expected_event_id must be a nonnegative integer")
    if not isinstance(expected_previous_hash, str):
        raise ValidationError("expected_previous_hash must be a string")
    if (
        not isinstance(expected_request, dict)
        or set(expected_request) != {"tool", "args"}
        or not isinstance(expected_request["tool"], str)
        or not expected_request["tool"]
        or not isinstance(expected_request["args"], dict)
    ):
        raise ValidationError("expected_request must be a concrete tool-and-args request")
    if (
        actual_event.get("event_id") != expected_event_id
        or actual_event.get("previous_hash") != expected_previous_hash
    ):
        raise StaleAnchor("forecast feedback does not match the selected real event and prefix")
    request = {"tool": actual_event.get("tool"), "args": actual_event.get("args")}
    if _signature(request) != _signature(expected_request):
        raise StaleAnchor("forecast feedback belongs to a different effect request")
    status = actual_event.get("status")
    if status not in {"pending", "returned", "raised", "interrupted_unknown"}:
        raise ValidationError("invalid actual event status")
    if status == "returned" and "value" not in actual_event:
        raise ValidationError("returned event is missing its value")
    if status == "returned":
        canonical_json(actual_event["value"])
    verdicts = []
    for forecast in checked["forecasts"]:
        result = (
            evaluate_predicate(forecast["predicate"], actual_event["value"])
            if status == "returned"
            else None
        )
        verdicts.append(
            {
                "candidate_id": forecast["candidate_id"],
                "verdict": "PASS" if result is True else "FAIL" if result is False else "UNKNOWN",
                "meaning": "local forecast on this exact event only; not program or task correctness",
            }
        )
    return {
        "diagnostic_id": checked["id"],
        "event_id": expected_event_id,
        "previous_hash": expected_previous_hash,
        "request": clone(expected_request),
        "event_status": status,
        "verdicts": verdicts,
        "contract_evidence": False,
    }


__all__ = [
    "DiagnosticReport",
    "check_forecasts",
    "evaluate_diagnostic",
    "proven_exclusive",
    "validate_diagnostic",
    "validate_predicate",
]
