# SPDX-License-Identifier: Apache-2.0
"""Strict historical replay of IR against the next actual recorded event.

No tool registry or callbacks are accepted. A changed request stops replay BEFORE
using its result; later records are never searched for a convenient match. Replayed
machines are hypothetical and must never be committed as live execution state.
"""

from __future__ import annotations

from typing import Any

from .errors import TraceIntegrityError, ValidationError
from .opaque import contains_opaque
from .values import canonical_json, clone


def _validate_records(records: Any) -> list[dict[str, Any]]:
    if not isinstance(records, (list, tuple)) or len(records) > 10000:
        raise ValidationError("replay expects at most 10000 event records")
    copied = clone(list(records))
    previous_id = None
    for record in copied:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("tool"), str)
            or not isinstance(record.get("args"), dict)
        ):
            raise TraceIntegrityError("each replay record needs tool and args")
        status = record.get("status")
        if not isinstance(status, str) or status not in {
            "returned",
            "raised",
            "pending",
            "interrupted_unknown",
        }:
            raise TraceIntegrityError("unrecognized historical event status")
        if status == "returned" and "value" not in record:
            raise TraceIntegrityError("returned historical event is missing its value")
        if status == "raised":
            error = record.get("error")
            if (
                not isinstance(error, dict)
                or not isinstance(error.get("type"), str)
                or not isinstance(error.get("message"), str)
            ):
                raise TraceIntegrityError("raised historical event needs type and message")
        if "event_id" in record:
            event_id = record["event_id"]
            if (
                type(event_id) is not int
                or event_id < 0
                or (previous_id is not None and event_id <= previous_id)
            ):
                raise TraceIntegrityError("historical event identifiers must increase")
            previous_id = event_id
    return copied


def replay(
    program: dict[str, Any],
    inputs: dict[str, Any] | None,
    records: list[dict[str, Any]],
    *,
    memory: dict[str, Any] | None = None,
    fuel: int = 50000,
    max_effects: int = 1000,
    max_value_bytes: int = 1048576,
) -> dict[str, Any]:
    """Re-run pure code and consume only exact consecutive historical requests.

    ``fuel`` applies to each pure segment; ``max_effects`` bounds consumed events.
    Full journal hash verification belongs to the trace store loading the records;
    this function validates event shapes/order and exact tool/argument agreement.
    It does not reconstruct world state or compare rewards of alternative actions.
    ``memory`` must be the memory available at the start of the replayed execution,
    not a final snapshot that could leak observations from later events.
    Historical opaque request/return state is not comparable without a live store;
    those frontiers are UNKNOWN. Other JSON fields in a receipt remain usable.
    """
    from .vm import new_machine, resume, run_until_boundary

    if type(max_effects) is not int or not 0 <= max_effects <= 10000:
        raise ValidationError("max_effects must be between 0 and 10000")
    if type(fuel) is not int or not 1 <= fuel <= 1000000:
        raise ValidationError("replay fuel must be between 1 and 1000000")
    if inputs is not None and not isinstance(inputs, dict):
        raise ValidationError("replay inputs must be an object")
    if memory is not None and not isinstance(memory, dict):
        raise ValidationError("replay memory must be the initial memory object")
    observed = _validate_records(records)
    machine = new_machine(program, clone(inputs or {}), mode="hypothetical")
    memory = clone(memory or {})
    index = 0

    def output(status: str, reason: str, boundary: Any) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "matched_records": index,
            "total_records": len(observed),
            "mode": "hypothetical",
            "frontier": {
                "kind": boundary.kind,
                "request": clone(boundary.request),
                "value": clone(boundary.value),
                "details": clone(boundary.details),
            },
            "machine": boundary.machine.to_dict(),
        }

    while True:
        # Only earlier consumed facts are visible to pure receipt reads, never future events.
        boundary = run_until_boundary(
            machine,
            receipts=observed[:index],
            memory=memory,
            fuel=fuel,
            max_value_bytes=max_value_bytes,
        )
        if boundary.kind == "return":
            if index != len(observed):
                return output("diverged", "program_returned_before_history_end", boundary)
            if contains_opaque(boundary.value):
                return output("unknown", "opaque_return_state_is_not_comparable", boundary)
            return output("completed", "all_events_matched", boundary)
        if boundary.kind == "replan":
            return output("unknown", "model_continuation_required", boundary)
        if boundary.kind != "effect":
            return output(boundary.kind, "non_effect_frontier", boundary)
        if index >= len(observed):
            return output("exhausted", "no_record_for_next_request", boundary)
        if index >= max_effects:
            return output("unknown", "replay_event_limit", boundary)
        record = observed[index]
        expected = {"tool": record["tool"], "args": record["args"]}
        if contains_opaque(boundary.request) or contains_opaque(expected):
            return output("unknown", "opaque_historical_request_is_not_comparable", boundary)
        if canonical_json(boundary.request) != canonical_json(expected):
            return output("diverged", "next_request_does_not_match_next_event", boundary)
        if record["status"] in {"pending", "interrupted_unknown"}:
            return output(
                "interrupted_unknown", "historical_effect_has_no_settled_outcome", boundary
            )
        outcome = {"status": record["status"]}
        if record["status"] == "returned":
            outcome["value"] = clone(record["value"])
        else:
            outcome["error"] = clone(record["error"])
        machine = resume(boundary.machine, outcome)
        index += 1
