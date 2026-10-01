# SPDX-License-Identifier: Apache-2.0
"""Bounded shapes of retained real states, never values or revision proposals."""

from flora.support.values import canonical_json


def _shape(machine):
    registers = machine["registers"]
    names = sorted(registers)[:32]
    types = {
        type(None): "null",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "array",
        dict: "object",
    }
    return {
        "block": machine["block_id"],
        "op_index": machine["op_index"],
        "register_types": {name: types.get(type(registers[name]), "unknown") for name in names},
        "registers_omitted": len(registers) - len(names),
    }


def revision_state(candidate, contexts, *, epoch, trace_digest):
    samples = [item for item in contexts if item["candidate_id"] == candidate.id]
    # Equal register shapes can have different observed histories. Preserve each
    # displayed checkpoint's identity rather than merging those distinct inputs.
    shapes = []
    for item in samples[:4]:
        context = item["context"]
        shapes.append(
            {
                **_shape(context["reference_machine"]),
                "context_id": context["id"],
                "receipt_count": len(context["receipts"]),
            }
        )
    # Read only current structure; don't clone registers or opaque payloads.
    machine = candidate.machine
    current = _shape(
        {"block_id": machine.block_id, "op_index": machine.op_index, "registers": machine.registers}
    )
    view = {
        **current,
        "anchor_current": candidate.anchor_epoch == epoch
        and candidate.anchor_digest == trace_digest,
        "mode": machine.mode,
        "machine_status": machine.status,
        "retained_context_count": len(samples),
        "checkpoint_shapes": shapes,
        "shapes_omitted": len(samples) - len(shapes),
        "checkpoints_omitted": len(samples) - len(shapes),
        "scope": "actual state shapes only; no compatibility verdict or proposed migration",
    }
    while len(canonical_json(view).encode("utf-8")) > 8192 and shapes:
        shapes.pop()
        view["shapes_omitted"] += 1
        view["checkpoints_omitted"] += 1
    return view
