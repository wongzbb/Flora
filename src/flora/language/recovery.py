# SPDX-License-Identifier: Apache-2.0
"""A bounded index into already-visible host evidence, never an inferred plan."""

from __future__ import annotations

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
"""


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
