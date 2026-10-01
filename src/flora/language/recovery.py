# SPDX-License-Identifier: Apache-2.0
"""A bounded index into already-visible host evidence, never an inferred plan."""

from __future__ import annotations

import json

RECOVERY_GUIDANCE = """CURRENT LOCAL-CONSUMER RECOVERY (not a fresh task):
compiler_recovery is an index into the visible host reports and ACTUAL settled
receipts, not new evidence. A pure consumer fault does not roll back earlier
external effects. Repair the remaining computation, not the whole original plan.
Use read_receipt(original_trace_index) to obtain the INNER recorded envelope;
check its status, then get value/error. Follow its actual shape: document content
is text until parse_json, not a field merged into the tool's returned object.
Do not repeat a successful read or mutation merely to recover its returned value.
A genuinely required new observation/action remains explicit and subject to the
usual grants, anchors and budgets; historical data is not proof of current state.
Do not infer missing/omitted receipts, successful outcomes, rollback, or PASS.
Unknown effects still halt. This index selects no candidate and changes no gate.
Each visible previous_programs.revision_state describes real current/checkpoint
register names and types, not their values or a PASS claim. When adapting an old
consumer to a newly observed shape, consider a model-written EXTEND revision with
a pure migration from context.inputs. Preserve covered behavior and let historical
AND current checks decide compatibility. If it cannot be justified, use a new
program without claiming inherited contract evidence. No revision is required.
"""


def revision_example():
    """Syntax teaching only; the live task's program and migration remain model-owned."""

    def get(dest, obj, field):
        return {"op": "get", "dest": dest, "args": [{"var": obj}, field]}

    program = [
        {
            "label": "main",
            "params": ["record"],
            "ops": [
                get("items", "record", "items"),
                {"op": "length", "dest": "count", "args": [{"var": "items"}]},
            ],
            "term": {"op": "return", "value": {"var": "count"}},
        }
    ]
    migration = [
        {
            "label": "main",
            "params": ["context"],
            "ops": [
                get("inputs", "context", "inputs"),
                get("reply", "inputs", "reply"),
                get("payload", "reply", "value"),
            ],
            "term": {"op": "return", "value": {"record": {"var": "payload"}}},
        }
    ]
    example = {
        "programs": [{"id": "main", "program": program, "inputs": {"record": {}}}],
        "incumbent": "main",
        "diagnostics": [],
        "expected_epoch": 1,
        "expected_digest": "0" * 64,
        "revisions": [
            {
                "id": "adapt_batch",
                "target_candidate": "main",
                "program": program,
                "migration": migration,
                "mode": "EXTEND",
            }
        ],
    }
    return (
        "HOST-AUTHORED SYNTAX EXAMPLE, not the current task or observed evidence: suppose an old "
        "main consumer's reply.value.count access faulted, and its ACTUAL reply.value has items. "
        "This proposal changes representation through a pure migration and asks for EXTEND "
        "checks. It is not automatically accepted; all retained history and current input "
        "must pass. Adapt the target, registers, full consumer and anchors to actual evidence. "
        "The activation inputs are structurally required but checked migration supplies their "
        "real values. Do not copy this example as a solution or repeat its old read.\n"
        + json.dumps(example, separators=(",", ":"))
    )


def recovery_view(view):
    """Project only current FAULTED candidates, current host faults and visible receipts.

    Omitted or stale evidence is never recovered from a second, hidden copy. Limit
    indices to eight each. Caller reapplies its ordinary context byte-size limit.
    Tool payloads/strings are never scanned for control words or instructions.
    """
    faulted = {p.get("id") for p in view["previous_programs"] if p.get("status") == "FAULTED"}
    faults = [
        r
        for r in view["reports"]
        if r.get("kind") == "local_execution"
        and r.get("status") == "fault"
        and r.get("epoch") == view["epoch"]
        and r.get("candidate") in faulted
    ][-8:]
    if not faults:
        return None
    return {
        "kind": "local_consumer_fault",
        "epoch": view["epoch"],
        "faults": [{"candidate": r["candidate"], "details": r.get("details", {})} for r in faults],
        "settled_receipts": [
            {
                "trace_index": r["trace_index"],
                "tool": r["record"].get("tool"),
                "status": r["record"]["status"],
            }
            for r in view["receipts"]
            if r["record"].get("status") in ("returned", "raised")
        ][-8:],
        "scope": "visible actual evidence only; original trace indices; not a replay or plan",
    }
