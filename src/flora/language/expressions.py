# SPDX-License-Identifier: Apache-2.0
"""Opt-in block-list-v2 pure expressions, mechanically lowered to SSA IR.

No evaluation, capability lookup, tool execution or inferred control flow occurs
here. Only expression positions in source programs are transformed. Observations,
inputs, forecast predicates, witnesses and escaped data are never interpreted.
"""

from __future__ import annotations

from flora.language.ir import OP_ARITIES
from flora.support.errors import ValidationError
from flora.support.values import clone


def lower_expressions(source):
    """Expand nested pure op/args into fresh, eager, block-local operations.

    Children precede parents; explicit operations retain their original order;
    terminator computations come last. The ordinary IR parser remains authoritative
    for scope, SSA, arity, continuations and limits. v1 syntax is unchanged.
    """
    program = clone(source)
    blocks = program.get("blocks") if isinstance(program, dict) else None
    if not isinstance(blocks, dict):
        return program  # The ordinary structural parser reports this error.
    total_ops = 0
    for label, block in blocks.items():
        if not isinstance(block, dict) or not isinstance(block.get("ops"), list):
            continue
        reserved = set()

        def reserve(value):
            if isinstance(value, dict):
                if set(value) == {"literal"}:
                    return
                # Include references, not just declarations: a fresh temporary
                # must never accidentally make an undefined source var valid.
                reserved.update(k for k in value if isinstance(k, str))
                for key, item in value.items():
                    if key in ("var", "dest", "bind") and isinstance(item, str):
                        reserved.add(item)
                    reserve(item)
            elif isinstance(value, list):
                for item in value:
                    reserve(item)

        reserve(block)
        if isinstance(block.get("params"), list):
            reserved.update(p for p in block["params"] if isinstance(p, str))
        operations = []
        serial = 0

        def emit(operation):
            nonlocal total_ops
            total_ops += 1
            if total_ops > 10_000:
                raise ValidationError("expanded block-list-v2 program exceeds 10000 operations")
            operations.append(operation)

        def expression(value):
            nonlocal serial
            if isinstance(value, list):
                return [expression(item) for item in value]
            if not isinstance(value, dict) or set(value) in ({"literal"}, {"var"}):
                return value
            if "op" in value and "args" in value:
                if set(value) != {"op", "args"}:
                    raise ValidationError(
                        f"block {label!r}: inline expressions require exactly op and args; "
                        "escape code-as-data with {literal:...}"
                    )
                op, args = value["op"], value["args"]
                if not isinstance(op, str) or op not in OP_ARITIES:
                    raise ValidationError(f"block {label!r}: unknown inline pure operation {op!r}")
                minimum, maximum = OP_ARITIES[op]
                if not isinstance(args, list) or not minimum <= len(args) <= maximum:
                    raise ValidationError(
                        f"block {label!r}: inline {op} expects {minimum}..{maximum} arguments"
                    )
                args = [expression(arg) for arg in args]
                while (dest := f"__flora_expr_{serial}") in reserved:
                    serial += 1
                serial += 1
                reserved.add(dest)
                emit({"op": op, "dest": dest, "args": args})
                return {"var": dest}
            return {key: expression(item) for key, item in value.items()}

        for operation in block["ops"]:
            if isinstance(operation, dict) and isinstance(operation.get("args"), list):
                operation["args"] = [expression(arg) for arg in operation["args"]]
            emit(operation)
        term = block.get("term")
        if isinstance(term, dict):
            for field in ("condition", "value", "reason", "state", "args", "capture"):
                if field not in term:
                    continue
                mapping = field == "capture" or (
                    field == "args" and term.get("op") in ("jump", "branch", "call", "alternative")
                )
                if mapping:
                    # Mapping keys are register names, even when named op/args.
                    if isinstance(term[field], dict):
                        term[field] = {key: expression(item) for key, item in term[field].items()}
                else:
                    term[field] = expression(term[field])
        block["ops"] = operations
    return program
