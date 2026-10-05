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


_SEMANTIC_OPS = {
    "const", "get", "get_default", "has", "set", "delete", "keys", "values",
    "length", "append", "extend", "slice", "add", "sub", "mul", "div", "mod",
    "eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "concat",
    "contains", "to_string", "parse_json", "read_receipt", "read_memory", "assert",
    "type",
}

_MAP_KEYS = {"for_each", "in", "yield"}


def _semantic_expression(value, path="expression"):
    """Normalize the small semantic expression language before lowering.

    The planner may use a readable ``get/from/path`` projection in addition to
    the canonical ``op/get`` form.  Both forms are data; neither is evaluated
    here. Ordinary records recurse into computed fields, while ``literal``
    preserves an intentionally opaque object so a payload is not mistaken for
    an operation.
    """
    if isinstance(value, list):
        return [_semantic_expression(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if not isinstance(value, dict):
        return clone(value)
    keys = set(value)
    if keys == {"var"} or keys == {"literal"}:
        return clone(value)
    if keys == {"get"}:
        projection = value["get"]
        if not isinstance(projection, dict) or set(projection) != {"from", "path"}:
            raise ValidationError(f"{path}.get requires from and path")
        path_value = projection["path"]
        if not isinstance(path_value, list) or len(path_value) > 32:
            raise ValidationError(f"{path}.get.path must be a list of at most 32 keys")
        result = _semantic_expression(projection["from"], f"{path}.get.from")
        for index, key in enumerate(path_value):
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise ValidationError(f"{path}.get.path[{index}] must be a string or integer")
            result = expr("get", result, key)
        return result
    if keys == _MAP_KEYS:
        binding = value["for_each"]
        if not isinstance(binding, list) or len(binding) != 2:
            raise ValidationError(f"{path}.for_each must be [KEY, VALUE]")
        if any(not isinstance(name, str) or NAME.fullmatch(name) is None for name in binding):
            raise ValidationError(f"{path}.for_each names must be valid identifiers")
        if binding[0] == binding[1]:
            raise ValidationError(f"{path}.for_each key and value names must differ")
        return {
            "for_each": list(binding),
            "in": _semantic_expression(value["in"], f"{path}.in"),
            "yield": _semantic_expression(value["yield"], f"{path}.yield"),
        }
    if keys == {"op", "args"}:
        op = value["op"]
        args = value["args"]
        if not isinstance(op, str) or op not in _SEMANTIC_OPS:
            raise ValidationError(f"{path}: unknown semantic operation {op!r}")
        if not isinstance(args, list):
            raise ValidationError(f"{path}.args must be a list")
        if op == "get_default" and len(args) == 2:
            # A common semantic spelling is defaulting an already projected
            # field: get_default({get:{from:OBJ,path:[KEY]}}, DEFAULT).
            # Normalize that sugar to the IR's explicit object/key/default
            # form. No value is inferred; missing fields still use the VM's
            # ordinary default behavior and all other arities remain strict.
            projection = args[0]
            if isinstance(projection, dict) and set(projection) == {"get"}:
                projection = projection["get"]
                if (
                    isinstance(projection, dict)
                    and set(projection) == {"from", "path"}
                    and isinstance(projection["path"], list)
                    and len(projection["path"]) == 1
                    and isinstance(projection["path"][0], (str, int))
                    and not isinstance(projection["path"][0], bool)
                ):
                    return {
                        "op": "get_default",
                        "args": [
                            _semantic_expression(projection["from"], f"{path}.args[0].get.from"),
                            projection["path"][0],
                            _semantic_expression(args[1], f"{path}.args[1]"),
                        ],
                    }
            raise ValidationError(
                f"{path}.get_default shorthand requires a one-key get projection; "
                "use object,key,default for the canonical form"
            )
        return {"op": op, "args": [_semantic_expression(item, f"{path}.args[{i}]")
                                      for i, item in enumerate(args)]}
    # A computed record is a useful result in its own right (for example
    # ``{"questions": {"var": "items"}}``).  Recurse through ordinary
    # object fields; ``literal`` remains available when the planner explicitly
    # wants an object containing unevaluated var/op-shaped data.
    return {key: _semantic_expression(item, f"{path}.{key}") for key, item in value.items()}


def _semantic_record(value, path="record"):
    """Normalize a record of argument/state fields without evaluating it."""
    if isinstance(value, dict) and set(value) in (
        {"var"}, {"literal"}, {"get"}, {"op", "args"}, _MAP_KEYS
    ):
        return _semantic_expression(value, path)
    if not isinstance(value, dict):
        return _semantic_expression(value, path)
    result = {}
    for key, item in value.items():
        child_path = f"{path}.{key}"
        # Nested records are common in contracts and tool arguments.  Recurse
        # as records unless the value is an explicit semantic wrapper.
        if isinstance(item, dict) and set(item) not in (
            {"var"}, {"literal"}, {"get"}, {"op", "args"}, _MAP_KEYS
        ):
            result[key] = _semantic_record(item, child_path)
        else:
            result[key] = _semantic_expression(item, child_path)
    return result


class _MapHoister:
    """Make map expressions compositional in ordinary expression positions.

    The VM intentionally has no hidden map primitive: maps lower to bounded
    loops.  A map nested in tool arguments, a condition, or a replan state must
    therefore be made an explicit preceding ``let`` in the same lexical scope.
    This is a language-level normalization, rather than a provider-specific
    repair, and keeps the observed value and contract data unchanged.
    """

    def __init__(self, source, inputs=()):
        self.reserved = set(inputs)
        self.serial = 0

        def collect(value):
            if isinstance(value, str):
                self.reserved.add(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    self.reserved.add(key)
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(source)

    def fresh(self):
        while True:
            name = f"__flora_map_expr_{self.serial}"
            self.serial += 1
            if name not in self.reserved:
                self.reserved.add(name)
                return name

    def expression(self, value, path="expression"):
        """Return (prefix lets, expression) for one arbitrary expression."""
        if isinstance(value, list):
            prefix, result = [], []
            for index, item in enumerate(value):
                child_prefix, child = self.expression(item, f"{path}[{index}]")
                prefix.extend(child_prefix)
                result.append(child)
            return prefix, result
        if not isinstance(value, dict) or set(value) in ({"var"}, {"literal"}):
            return [], clone(value)
        if set(value) == _MAP_KEYS:
            in_prefix, source = self.expression(value["in"], f"{path}.in")
            yield_prefix, yielded = self.expression(value["yield"], f"{path}.yield")
            name = self.fresh()
            # Yield computations stay inside the outer iteration so nested
            # maps can use its bindings without leaking them into outer scope.
            prefix = in_prefix + [
                {"let": name, "value": {"literal": []}},
                {"for_each": list(value["for_each"]), "in": source,
                 "steps": yield_prefix + [
                     {"let": name, "value": expr("append", ref(name), yielded)}
                 ]},
            ]
            return prefix, ref(name)
        prefix, result = [], {}
        for key, item in value.items():
            child_prefix, child = self.expression(item, f"{path}.{key}")
            prefix.extend(child_prefix)
            result[key] = child
        return prefix, result


def _normalize_semantic_plan(source, inputs=(), hoister=None):
    """Copy and normalize every expression position in a semantic plan."""
    source = clone(source)
    if not isinstance(source, dict):
        raise ValidationError("structured program must be an object")
    if not isinstance(source.get("steps"), list):
        raise ValidationError("structured steps require a list")
    hoister = hoister or _MapHoister(source, inputs)

    def steps(items, path):
        result = []
        for index, step in enumerate(items):
            if not isinstance(step, dict):
                raise ValidationError(f"{path}[{index}] must be an object")
            keys = set(step)
            item = clone(step)
            where = f"{path}[{index}]"
            if keys == {"let", "value"}:
                item["value"] = _semantic_expression(item["value"], where + ".value")
                prefix, item["value"] = hoister.expression(item["value"], where + ".value")
                result.extend(prefix)
            elif (
                keys in ({"call", "save"}, {"call", "args", "save"})
                and item.get("call") in {"read_receipt", "read_memory"}
            ):
                # These are pure observations, not effect tools. Accept the
                # readable call-shaped spelling at the semantic boundary and
                # lower it to a let/op so it cannot enter the side-effect
                # journal or be replayed. All other calls remain effects.
                if item.get("call") == "read_receipt":
                    raw_args = item.get("args")
                    if isinstance(raw_args, dict) and set(raw_args) == {"index"}:
                        raw_args = [raw_args["index"]]
                    elif not isinstance(raw_args, list):
                        raw_args = [raw_args]
                else:
                    raw_args = item.get("args", [])
                    if isinstance(raw_args, dict):
                        if set(raw_args) <= {"key", "default"}:
                            raw_args = [raw_args[key] for key in ("key", "default") if key in raw_args]
                        else:
                            raise ValidationError(
                                f"{where}.{item['call']} pure call args require key/default"
                            )
                    elif not isinstance(raw_args, list):
                        raw_args = [raw_args]
                value = {"op": item["call"], "args": raw_args}
                value = _semantic_expression(value, where + ".args")
                prefix, value = hoister.expression(value, where + ".args")
                result.extend(prefix)
                result.append({"let": item["save"], "value": value})
                continue
            elif keys in (
                {"call", "save"}, {"call", "save", "on_error"},
                {"call", "args", "save"}, {"call", "args", "save", "on_error"},
            ):
                item.setdefault("args", {})
                item["args"] = _semantic_record(item["args"], where + ".args")
                prefix, item["args"] = hoister.expression(item["args"], where + ".args")
                if prefix:
                    result.extend(prefix)
                if "on_error" in item:
                    handler = item["on_error"]
                    if not isinstance(handler, dict) or set(handler) not in ({"save"}, {"save", "plan"}):
                        raise ValidationError(where + ".on_error requires save, optionally plan")
                    if "plan" in handler:
                        nested = handler["plan"]
                        if isinstance(nested, dict) and set(nested) in ({"return"}, {"replan"}):
                            nested = {"steps": [], **nested}
                        handler["plan"] = _normalize_semantic_plan(nested, hoister=hoister)
            elif keys in ({"if", "then"}, {"if", "then", "else"}):
                item["if"] = _semantic_expression(item["if"], where + ".if")
                prefix, item["if"] = hoister.expression(item["if"], where + ".if")
                if prefix:
                    result.extend(prefix)
                item["then"] = steps(item["then"], where + ".then")
                # An omitted else branch is the semantic no-op branch. This
                # keeps the action language total for models that express a
                # one-sided guard, while the lowered IR remains explicit.
                item["else"] = steps(item.get("else", []), where + ".else")
            elif keys == {"for_each", "in", "steps"}:
                item["in"] = _semantic_expression(item["in"], where + ".in")
                prefix, item["in"] = hoister.expression(item["in"], where + ".in")
                if prefix:
                    result.extend(prefix)
                item["steps"] = steps(item["steps"], where + ".steps")
            elif keys in ({"return"}, {"replan"}):
                # A nested terminal is checked by the normal planner; keep it
                # here only to produce a precise unreachable-statement error.
                if "return" in item:
                    item["return"] = _semantic_expression(item["return"], where + ".return")
            else:
                raise ValidationError(f"{where}: unsupported semantic statement keys {sorted(keys)!r}")
            result.append(item)
        return result

    source["steps"] = steps(source["steps"], "steps")
    if set(source) == {"steps", "return"}:
        source["return"] = _semantic_expression(source["return"], "return")
        prefix, source["return"] = hoister.expression(source["return"], "return")
        source["steps"].extend(prefix)
    elif set(source) == {"steps", "replan"}:
        repl = source["replan"]
        if isinstance(repl, dict) and set(repl) == {"reason", "state"}:
            repl["reason"] = _semantic_expression(repl["reason"], "replan.reason")
            repl["state"] = _semantic_record(repl["state"], "replan.state")
            reason_prefix, repl["reason"] = hoister.expression(repl["reason"], "replan.reason")
            state_prefix, repl["state"] = hoister.expression(repl["state"], "replan.state")
            source["steps"].extend(reason_prefix + state_prefix)
    return source


class _Lowerer:
    def __init__(self, source, inputs):
        self.blocks = {}
        self.serial = 0
        self.count = 0
        self.effects = 0
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

    @classmethod
    def _definite_assignments(cls, steps, incoming=()):
        """Return names defined on every path that reaches the sequence end.

        Structured plans are lowered into SSA blocks. A name introduced in one
        branch used to disappear at the join even when both branches assigned
        it, forcing models to duplicate an entire final return. Compute only
        the intersection of branch definitions; a name assigned in one branch
        or in a possibly empty loop remains local and still fails closed if a
        later step uses it.
        """
        scope = set(incoming)
        for step in steps:
            keys = set(step)
            if keys in ({"let", "value"}, {"call", "save"}, {"call", "args", "save"},
                        {"call", "save", "on_error"}, {"call", "args", "save", "on_error"}):
                scope.add(step["let"] if "let" in step else step["save"])
            elif keys == {"if", "then", "else"}:
                then_scope = cls._definite_assignments(step["then"], scope)
                else_scope = cls._definite_assignments(step["else"], scope)
                scope = then_scope & else_scope
            elif keys == {"for_each", "in", "steps"}:
                # The body may execute zero times, so loop-local assignments
                # cannot be definite after the loop.
                continue
            elif keys in ({"return"}, {"replan"}):
                return scope
        return scope

    @classmethod
    def _assignment_order(cls, steps):
        """List assignment names in source order for deterministic joins."""
        names = []
        for step in steps:
            keys = set(step)
            if "let" in keys:
                name = step["let"]
                if name not in names:
                    names.append(name)
            elif "save" in keys:
                name = step["save"]
                if name not in names:
                    names.append(name)
                handler = step.get("on_error")
                if isinstance(handler, dict) and "save" in handler:
                    error = handler["save"]
                    if error not in names:
                        names.append(error)
                if isinstance(handler, dict) and isinstance(handler.get("plan"), dict):
                    for nested in cls._assignment_order(handler["plan"].get("steps", [])):
                        if nested not in names:
                            names.append(nested)
            elif keys == {"if", "then", "else"}:
                for nested_steps in (step["then"], step["else"]):
                    for nested in cls._assignment_order(nested_steps):
                        if nested not in names:
                            names.append(nested)
            elif keys == {"for_each", "in", "steps"}:
                # Loop bindings and body locals do not escape the loop.
                continue
        return names

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
            mapped = step["value"]
            if isinstance(mapped, dict) and set(mapped) == {"for_each", "in", "yield"}:
                # Map expressions are lowered to the same bounded accumulator
                # loop as mapped returns.  This lets a planner use a map as a
                # value in a record or assignment without adding a new runtime
                # primitive.
                mapped_steps = [
                    {"let": name, "value": {"literal": []}},
                    {
                        "for_each": mapped["for_each"],
                        "in": mapped["in"],
                        "steps": [{
                            "let": name,
                            "value": {"op": "append", "args": [
                                {"var": name}, mapped["yield"]
                            ]},
                        }],
                    },
                ] + [step for step in rest]
                return self.sequence(mapped_steps, scope, finish, depth)
            after = scope if name in scope else (*scope, name)
            target = self.sequence(rest, after, finish, depth)
            return self.jump(scope, target, {**self.frame(scope), name: step["value"]})
        if keys in ({"call", "args", "save"}, {"call", "args", "save", "on_error"}):
            self.effects += 1
            if self.effects > 32:
                raise ValidationError("structured phase exceeds 32 effect calls")
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
                if not isinstance(handler, dict) or set(handler) not in ({"save"}, {"save", "plan"}):
                    raise ValidationError("on_error requires save, optionally plan")
                error = self.name(handler["save"])
                if "plan" in handler:
                    error_scope = scope if error in scope else (*scope, error)
                    error_args = {**self.frame(scope), error: expr("get", ref(reply), "error")}
                    error_target = self.plan(handler["plan"], error_scope, depth + 1)
                    failed = self.jump(resume_scope, error_target, error_args)
                else:
                    # Saving a tool fault is useful even when the planner does
                    # not know a recovery branch yet.  Preserve the observed
                    # error and route to a replan rather than rejecting the
                    # entire semantic phase over optional syntax.
                    error_scope = (*scope, reply) if error in scope else (*scope, reply, error)
                    error_args = {**self.frame(scope), reply: ref(reply),
                                  **({} if error in scope else {error: expr("get", ref(reply), "error")})}
                    replan_target = self.block(error_scope, {
                        "op": "replan",
                        "reason": "Tool raised; decide from the saved error observation",
                        "state": {
                            "error": ref(error),
                            "outcome": ref(reply),
                            "variables": self.frame(scope),
                        },
                    })
                    failed = self.jump(resume_scope, replan_target, error_args)
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
            then_definite = self._definite_assignments(step["then"], scope)
            else_definite = self._definite_assignments(step["else"], scope)
            common = then_definite & else_definite
            branch_names = self._assignment_order(step["then"] + step["else"])
            merged = tuple(
                name for name in branch_names if name in common and name not in scope
            )
            target_scope = (*scope, *merged)
            target = self.sequence(rest, target_scope, finish, depth)

            def join(branch_scope):
                # A name introduced on both branches is a definite SSA value
                # at this join. One-sided branch locals stay local, while
                # assignments to existing names retain their old behavior.
                captures = {
                    **self.frame(scope),
                    **{name: ref(name) for name in merged},
                }
                return self.jump(branch_scope, target, captures)

            yes = self.sequence(step["then"], scope, join, depth + 1)
            no = self.sequence(step["else"], scope, join, depth + 1)
            return self.block(scope, {"op": "branch", "condition": step["if"],
                                      "yes": yes, "no": no, "args": self.frame(scope)})
        if keys == {"for_each", "in", "steps"}:
            mapped_source = step["in"]
            if isinstance(mapped_source, dict) and set(mapped_source) == {"for_each", "in", "yield"}:
                temp = self.fresh()
                return self.sequence([
                    {"let": temp, "value": mapped_source},
                    {**step, "in": {"var": temp}},
                    *rest,
                ], scope, finish, depth)
            binding = step["for_each"]
            if isinstance(binding, str):
                names = (self.name(binding),)
            elif isinstance(binding, list) and len(binding) == 2:
                names = tuple(self.name(item) for item in binding)
                if names[0] == names[1]:
                    raise ValidationError("for_each key and value names must differ")
            else:
                raise ValidationError("for_each must name one item or [key,value]")
            if any(item in scope for item in names):
                raise ValidationError("for_each item must not shadow an existing variable")
            target = self.sequence(rest, scope, finish, depth)
            values, index, head = self.fresh(), self.fresh(), self.fresh()
            loop_scope = (*scope, values, index)
            body_scope = (*loop_scope, *names)

            def advance(current):
                return self.jump(current, head, {**self.frame(loop_scope),
                                                index: expr("add", ref(index), 1)})

            body = self.sequence(step["steps"], body_scope, advance, depth + 1)
            item_args = {names[-1]: expr("get", ref(values), ref(index))}
            if len(names) == 2:
                item_args[names[0]] = ref(index)
            enter = self.jump(loop_scope, body, {**self.frame(loop_scope), **item_args})
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
    source = _normalize_semantic_plan(source, inputs)
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
