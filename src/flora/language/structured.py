# SPDX-License-Identifier: Apache-2.0
"""Structured actions compiled to the existing effect/continuation IR.

The model owns actions, conditions, data and contracts. This compiler owns only
lexical scope, SSA transfers, loop state and outcome dispatch. No effect executes
here; no failed program is guessed, repaired, or treated as a successful task.
"""

from flora.language.expressions import lower_expressions
from flora.language.ir import NAME, parse_program
from flora.support.errors import ValidationError
from flora.support.values import clone


def ref(name):
    return {"var": name}


def expr(op, *args):
    return {"op": op, "args": list(args)}


class _Lowerer:
    def __init__(self, source, inputs):
        self.blocks = {}
        self.serial = 0
        self.count = 0
        self.reserved = set(inputs)

        def reserve(value):
            if isinstance(value, str):
                self.reserved.add(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    self.reserved.add(key)
                    reserve(item)
            elif isinstance(value, list):
                for item in value:
                    reserve(item)

        reserve(source)

    def fresh(self):
        while True:
            name = f"__flora_s_{self.serial}"
            self.serial += 1
            if name not in self.reserved:
                self.reserved.add(name)
                return name

    def block(self, scope, term, *, label=None):
        if len(self.blocks) >= 512:
            raise ValidationError("structured program exceeds 512 compiled blocks")
        label = label or self.fresh()
        self.blocks[label] = {"params": list(scope), "ops": [], "term": term}
        return label

    def jump(self, scope, target, args=None):
        return self.block(scope, {"op": "jump", "target": target,
                                  "args": self.frame(scope) if args is None else args})

    @staticmethod
    def frame(scope):
        return {key: ref(key) for key in scope}

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not NAME.fullmatch(value):
            raise ValidationError("structured variable must be a valid identifier")
        return value

    def terminal(self, source, scope):
        if set(source) == {"return"}:
            return self.block(scope, {"op": "return", "value": source["return"]})
        if set(source) == {"replan"}:
            value = source["replan"]
            if isinstance(value, dict) and set(value) == {"reason", "state"}:
                return self.block(scope, {"op": "replan", **value})
        raise ValidationError("structured end requires return or replan:{reason,state}")

    def sequence(self, steps, scope, finish, depth=0):
        if depth > 32 or not isinstance(steps, list):
            raise ValidationError("structured steps require a list with nesting at most 32")
        if not steps:
            return finish(scope)
        step, *rest = steps
        self.count += 1
        if self.count > 128 or not isinstance(step, dict):
            raise ValidationError("structured program requires at most 128 action statements")
        keys = set(step)
        if keys in ({"return"}, {"replan"}):
            if rest:
                raise ValidationError("unreachable statements after structured return/replan")
            return self.terminal(step, scope)
        if keys == {"let", "value"}:
            name = self.name(step["let"])
            after = scope if name in scope else (*scope, name)
            target = self.sequence(rest, after, finish, depth)
            return self.jump(scope, target, {**self.frame(scope), name: step["value"]})
        if keys in ({"call", "args", "save"}, {"call", "args", "save", "on_error"}):
            name = self.name(step["save"])
            after = scope if name in scope else (*scope, name)
            target = self.sequence(rest, after, finish, depth)
            reply = self.fresh()
            resume_scope = (*scope, reply)
            ok = self.jump(resume_scope, target,
                           {**self.frame(scope), name: expr("get", ref(reply), "value")})
            # Default error policy is explicit in the source language: preserve
            # the actual error/state for a new decision, never retry or continue.
            if "on_error" in step:
                handler = step["on_error"]
                if not isinstance(handler, dict) or set(handler) != {"save", "plan"}:
                    raise ValidationError("on_error requires save and plan")
                error = self.name(handler["save"])
                error_scope = scope if error in scope else (*scope, error)
                error_target = self.plan(handler["plan"], error_scope, depth + 1)
                failed = self.jump(resume_scope, error_target,
                                   {**self.frame(scope), error: expr("get", ref(reply), "error")})
            else:
                failed = self.block(resume_scope, {
                    "op": "replan", "reason": "Tool raised; decide from its actual observation",
                    "state": {"tool": {"literal": step["call"]}, "outcome": ref(reply),
                              "variables": self.frame(scope)},
                })
            check = self.block(resume_scope, {
                "op": "branch", "condition": expr("eq", expr("get", ref(reply), "status"), "returned"),
                "yes": ok, "no": failed, "args": self.frame(resume_scope),
            })
            return self.block(scope, {"op": "effect", "tool": step["call"], "args": step["args"],
                                      "resume": check, "bind": reply, "capture": self.frame(scope)})
        if keys == {"if", "then", "else"}:
            target = self.sequence(rest, scope, finish, depth)

            def join(branch_scope):
                # New branch locals stay local; assignments to existing names
                # cross the join. Both successors receive the same entry scope.
                return self.jump(branch_scope, target, self.frame(scope))

            yes = self.sequence(step["then"], scope, join, depth + 1)
            no = self.sequence(step["else"], scope, join, depth + 1)
            return self.block(scope, {"op": "branch", "condition": step["if"],
                                      "yes": yes, "no": no, "args": self.frame(scope)})
        if keys == {"for_each", "in", "steps"}:
            item = self.name(step["for_each"])
            if item in scope:
                raise ValidationError("for_each item must not shadow an existing variable")
            target = self.sequence(rest, scope, finish, depth)
            values, index, head = self.fresh(), self.fresh(), self.fresh()
            loop_scope = (*scope, values, index)
            body_scope = (*loop_scope, item)

            def advance(current):
                return self.jump(current, head, {**self.frame(loop_scope),
                                                index: expr("add", ref(index), 1)})

            body = self.sequence(step["steps"], body_scope, advance, depth + 1)
            enter = self.jump(loop_scope, body, {**self.frame(loop_scope),
                                                item: expr("get", ref(values), ref(index))})
            done = self.jump(loop_scope, target, self.frame(scope))
            self.block(loop_scope, {"op": "branch",
                                    "condition": expr("lt", ref(index), expr("length", ref(values))),
                                    "yes": enter, "no": done, "args": self.frame(loop_scope)}, label=head)
            return self.jump(scope, head, {**self.frame(scope), values: step["in"], index: 0})
        raise ValidationError("structured statement must be let/value, call/args/save, if/then/else, "
                              "for_each/in/steps, return, or replan")

    def plan(self, source, scope, depth=0):
        if not isinstance(source, dict) or set(source) not in ({"steps", "return"}, {"steps", "replan"}):
            raise ValidationError("structured program requires steps and exactly one return or replan")
        end = {key: value for key, value in source.items() if key != "steps"}
        return self.sequence(source["steps"], tuple(scope), lambda names: self.terminal(end, names), depth)


def lower_plan(source, inputs=()):
    """Build lexical continuations without evaluating expressions or tool calls."""
    source = clone(source)
    if any(not isinstance(name, str) or not NAME.fullmatch(name) for name in inputs):
        raise ValidationError("structured inputs must have identifier keys")
    compiler = _Lowerer(source, inputs)
    entry = compiler.plan(source, inputs)
    return parse_program(lower_expressions({"version": 1, "entry": entry, "blocks": compiler.blocks}))


def lower_bundle(source):
    """Keep anchors, contracts, diagnostics, revisions and values unchanged."""
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
            inputs = item.get("inputs", {})
            if not isinstance(inputs, dict):
                raise ValidationError("structured program inputs must be an object")
            if collection == "revisions":
                # A revision's entry interface is explicit; its migration must
                # return that object. It is not inferred from old checkpoints.
                inputs = item.pop("parameters", [])
                if not isinstance(inputs, list) or len(set(inputs)) != len(inputs):
                    raise ValidationError("structured revision parameters must be unique")
            item["program"] = lower_plan(item["program"], inputs)
            if collection == "revisions":
                if "migration" not in item:
                    raise ValidationError("revision requires an explicit migration")
                item["migration"] = lower_plan(item["migration"], ["context"])
    return bundle
