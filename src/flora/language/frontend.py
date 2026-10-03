# SPDX-License-Identifier: Apache-2.0
"""Versioned, mechanical syntax sugar; no planning, execution or inferred outcomes.

observe-v1 adds exactly one terminator. Both outcome consumers are mandatory.
Everything is lowered to ordinary IR before capability, anchor, diagnostic and
revision validation. The runtime, contracts and evidence store see only IR v1.
"""

from __future__ import annotations

from flora.language.ir import NAME, parse_program
from flora.support.errors import ValidationError
from flora.support.values import clone

OBSERVE_GUIDANCE = r"""Optional observe-v1 terminator (locally expanded to ordinary effect/branch IR):
{"op":"observe","tool":"TOOL","args":{},"bind":"result","capture":{},
 "success":"success_block","error":"error_block"}
Both targets are mandatory; each has params equal to capture keys plus bind.
Success receives the raw returned value; error receives the error object, NOT
reply envelopes. Capture expressions are evaluated before the effect. There is
no implicit error strategy, retry, replan or inferred result; interrupted effects
still halt. Keep pure result consumers/branches; replan only for new reasoning.
All ordinary IR, candidates, diagnostics and revisions remain available and checked.
"""


def _observation_signature(block_id, term, blocks, *, project_captures=False):
    if set(term) != {"op", "tool", "args", "bind", "capture", "success", "error"}:
        raise ValidationError("observe requires tool, args, bind, capture, success and error")
    bind, capture = term["bind"], term["capture"]
    if (
        not isinstance(bind, str)
        or not NAME.fullmatch(bind)
        or not isinstance(capture, dict)
        or bind in capture
        or any(not isinstance(k, str) or not NAME.fullmatch(k) for k in capture)
    ):
        raise ValidationError("observe requires valid capture identifiers distinct from bind")
    params = list(capture) + [bind]
    errors = []
    for label in ("success", "error"):
        target = term[label]
        if (
            not isinstance(target, str)
            or target not in blocks
            or not isinstance(blocks[target], dict)
            or not isinstance(blocks[target].get("params"), list)
            or any(not isinstance(p, str) for p in blocks[target]["params"])
            or (
                not (
                    len(set(blocks[target]["params"]) - set(capture)) == 1
                )
                if project_captures
                else set(blocks[target]["params"]) != set(params)
            )
        ):
            actual = (
                blocks.get(target, {}).get("params")
                if isinstance(target, str) and isinstance(blocks.get(target), dict)
                else None
            )
            requirement = (
                f"one result parameter and only an explicitly declared subset of capture keys; "
                f"allowed params {params!r}"
                if project_captures
                else f"capture keys plus bind; expected params {params!r}"
            )
            errors.append(
                f"observe in block {block_id!r}: {label} target {target!r} must accept "
                f"{requirement}, got {actual!r}"
            )
    if errors:
        raise ValidationError("\n".join(errors))
    return bind, capture, params


def lower_program(source: dict, *, project_captures=False) -> dict:
    """Expand explicit observations, preserving expressions, targets and data literals."""
    program = clone(source)
    if not isinstance(program, dict) or not isinstance(program.get("blocks"), dict):
        raise ValidationError("observe-v1 requires a program with blocks")
    blocks = program["blocks"]
    if not 1 <= len(blocks) <= 512:
        raise ValidationError("observe-v1 requires 1..512 source blocks")
    if any(not isinstance(k, str) for k in blocks):
        raise ValidationError("observe-v1 block names must be strings")
    for block_id in sorted(list(blocks)):
        block = blocks[block_id]
        if not isinstance(block, dict) or not isinstance(block.get("term"), dict):
            raise ValidationError("observe-v1 requires block terminators")
        term = block["term"]
        if term.get("op") != "observe":
            continue
        bind, capture, params = _observation_signature(
            block_id, term, blocks, project_captures=project_captures
        )
        # Fresh block names and local temporaries cannot capture user identifiers.
        n = 0
        while True:
            prefix = f"__flora_observe_{n}"
            check, ok, error = [prefix + suffix for suffix in ("_check", "_ok", "_error")]
            reply, status, test = [prefix + suffix for suffix in ("_reply", "_status", "_test")]
            if not {check, ok, error} & blocks.keys() and not {reply, status, test} & set(params):
                break
            n += 1

        def var(name):
            return {"var": name}

        forwarded = {key: var(key) for key in capture}
        frame = {**forwarded, reply: var(reply)}
        block["term"] = {
            "op": "effect",
            "tool": term["tool"],
            "args": term["args"],
            "resume": check,
            "bind": reply,
            "capture": capture,
        }
        blocks[check] = {
            "params": list(capture) + [reply],
            "ops": [
                {"op": "get", "dest": status, "args": [var(reply), "status"]},
                {"op": "eq", "dest": test, "args": [var(status), "returned"]},
            ],
            "term": {"op": "branch", "condition": var(test), "yes": ok, "no": error, "args": frame},
        }
        for name, field, target in (
            (ok, "value", term["success"]),
            (error, "error", term["error"]),
        ):
            result_name = bind
            if project_captures:
                # A target may use a local name for the raw outcome. The
                # unique parameter outside the declared captures receives the
                # result; this is interface alpha-renaming, not value inference.
                target_params = set(blocks[target]["params"])
                result_name = next(iter(target_params - set(capture)))
                # Evaluate and carry EVERY capture exactly as before. Only the
                # explicit final jump drops values the target did not declare.
                # Dropping a capture expression before the effect would change
                # fault/resource behavior and is not this source shorthand.
            arguments = {**forwarded, result_name: var(bind)}
            if project_captures:
                arguments = {
                    key: value
                    for key, value in arguments.items()
                    if key in blocks[target]["params"]
                }
            blocks[name] = {
                "params": list(capture) + [reply],
                "ops": [{"op": "get", "dest": bind, "args": [var(reply), field]}],
                "term": {"op": "jump", "target": target, "args": arguments},
            }
        if len(blocks) > 512:
            raise ValidationError("expanded observe-v1 program exceeds 512 IR blocks")
    return parse_program(program)


def _check_source_expressions(program):
    """Reject ambiguous operation-as-data typos, never execute or infer an op.

    block-list-v1 reserves op/args expression objects. Actual code-as-data is still
    fully expressible via {"literal": ...}; observed tool values are never scanned.
    Legacy ir-v1 and observe-v1 keep their original unrestricted data syntax.
    """

    def visit(expr, location):
        if isinstance(expr, dict):
            if set(expr) == {"literal"}:
                return
            if set(expr) == {"op", "args"} and isinstance(expr["op"], str):
                raise ValidationError(
                    f"{location}: inline operation {expr['op']!r} is not an expression; "
                    "put it in ops with a dest and reference {var:dest}. "
                    "For intentional code-as-data, wrap the object in {literal:...}"
                )
            for value in expr.values():
                visit(value, location)
        elif isinstance(expr, list):
            for value in expr:
                visit(value, location)

    for label, block in program.get("blocks", {}).items():
        if not isinstance(block, dict):
            continue  # Structural validation below reports the malformed block.
        for op in block.get("ops", []) if isinstance(block.get("ops"), list) else []:
            if isinstance(op, dict):
                visit(op.get("args"), f"block {label!r} operation args")
        term = block.get("term")
        if isinstance(term, dict):
            for field in ("value", "condition", "args", "capture", "reason", "state"):
                if field in term:
                    visit(term[field], f"block {label!r} term.{field}")


def lower_block_list(source, *, inline_expressions=False, project_captures=False) -> dict:
    """First labelled block is the entry; no new execution or inferred control flow."""
    if isinstance(source, dict):
        program = source
    else:
        if not isinstance(source, list) or not 1 <= len(source) <= 512:
            raise ValidationError("block-list-v1 requires 1..512 labelled blocks")
        blocks = {}
        for block in source:
            if isinstance(block, dict):
                # A few OpenAI-compatible models place continuation metadata next
                # to the block instead of inside its control term. Normalize only
                # this unambiguous envelope variant; no values or branches are
                # inferred, and ordinary structural validation still follows.
                extras = set(block) - {"label", "params", "ops", "term"}
                control = {"resume", "bind", "capture", "success", "error", "target", "args", "branches"}
                term = block.get("term")
                control_ops = {
                    "return",
                    "replan",
                    "jump",
                    "branch",
                    "call",
                    "alternative",
                    "effect",
                    "observe",
                }
                if (
                    isinstance(term, dict)
                    and isinstance(term.get("op"), str)
                    and term.get("op") not in control_ops
                    and {"resume", "bind", "capture"} <= set(term)
                    and "tool" not in term
                ):
                    # Accept a generic tool-term shorthand only when its
                    # continuation metadata is complete. Tool arguments remain
                    # data and are validated unchanged by the normal frontend.
                    tool_name = term["op"]
                    args = {
                        key: value
                        for key, value in term.items()
                        if key not in {"op", "resume", "bind", "capture"}
                    }
                    term = {
                        "op": "effect",
                        "tool": tool_name,
                        "args": args,
                        "resume": term["resume"],
                        "bind": term["bind"],
                        "capture": term["capture"],
                    }
                    block = {**block, "term": term}
                if (
                    isinstance(term, dict)
                    and set(term)
                    == {"op", "tool", "args", "bind", "capture", "success", "error"}
                    and term.get("op") == "effect"
                ):
                    # Some model outputs use the ordinary effect name while
                    # supplying the complete observe-v1 outcome pair. This is
                    # an unambiguous semantic envelope; normalize its opcode,
                    # then run the same observe signature and IR checks.
                    term = {**term, "op": "observe"}
                    block = {**block, "term": term}
                if extras and extras <= control and isinstance(term, dict) and "op" in term:
                    if not (extras & set(term)):
                        block = {
                            **{key: value for key, value in block.items() if key not in extras},
                            "term": {**term, **{key: block[key] for key in extras}},
                        }
            if not isinstance(block, dict) or set(block) != {"label", "params", "ops", "term"}:
                raise ValidationError("block-list-v1 blocks require label, params, ops and term")
            label = block["label"]
            if not isinstance(label, str) or not NAME.fullmatch(label) or label in blocks:
                raise ValidationError("block-list-v1 requires valid unique block labels")
            blocks[label] = {key: value for key, value in block.items() if key != "label"}
        program = {"version": 1, "entry": next(iter(blocks)), "blocks": blocks}
    # Apply the same unambiguous observe envelope normalization to the
    # dictionary form as to the labelled-list form.  Models may return either
    # source shape; the semantic lowering and validation remain identical.
    if isinstance(program.get("blocks"), dict):
        for label, block in program["blocks"].items():
            term = block.get("term") if isinstance(block, dict) else None
            if (
                isinstance(term, dict)
                and set(term) == {"op", "tool", "args", "bind", "capture", "success", "error"}
                and term.get("op") == "effect"
            ):
                program["blocks"][label] = {
                    **block,
                    "term": {**term, "op": "observe"},
                }
    if inline_expressions:
        from flora.language.expressions import lower_expressions

        program = lower_expressions(program)
    if isinstance(program.get("blocks"), dict):
        if not inline_expressions:
            _check_source_expressions(program)
        errors = []
        for label, block in program["blocks"].items():
            term = block.get("term") if isinstance(block, dict) else None
            if isinstance(term, dict) and term.get("op") == "observe":
                try:
                    _observation_signature(
                        label, term, program["blocks"], project_captures=project_captures
                    )
                except ValidationError as exc:
                    errors.append(str(exc))
                    if len(errors) == 16:
                        break
        if errors:
            raise ValidationError("Source observation errors:\n" + "\n".join(errors))
    return lower_program(program, project_captures=project_captures)


def lower_bundle(source: dict, *, syntax="observe-v1") -> dict:
    """Only program-bearing fields are rewritten; never recurse through user data."""
    if syntax not in ("observe-v1", "block-list-v1", "block-list-v2", "block-list-v3"):
        raise ValidationError("unsupported frontend syntax")

    def lower(program):
        if syntax == "observe-v1":
            return lower_program(program)
        return lower_block_list(
            program,
            inline_expressions=syntax in ("block-list-v2", "block-list-v3"),
            project_captures=syntax == "block-list-v3",
        )

    bundle = clone(source)
    if not isinstance(bundle, dict):
        raise ValidationError("compiler bundle must be an object")
    for collection in ("programs", "diagnostics", "revisions"):
        items = bundle.get(collection, [])
        if not isinstance(items, list):
            raise ValidationError(collection + " must be a list")
        for item in items:
            if not isinstance(item, dict) or "program" not in item:
                raise ValidationError(collection + " requires program objects")
            item["program"] = lower(item["program"])
            if collection == "revisions":
                if "migration" not in item:
                    raise ValidationError("revision requires an explicit migration")
                item["migration"] = lower(item["migration"])
    return bundle
