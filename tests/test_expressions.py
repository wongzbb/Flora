# SPDX-License-Identifier: Apache-2.0
"""Pure source expressions preserve core IR execution and versioned old syntax."""

import copy
import json
import unittest

from flora.engine.runtime import Runtime
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler
from flora.language.frontend import lower_bundle
from flora.language.prompts import compact_prompt, examples
from flora.language.vm import new_machine, run_until_boundary
from flora.support.errors import CompilerError, ValidationError
from tests.helpers import bundle, context, pure
from tests.test_compact import listed
from tests.test_recovery import SequenceProvider


def expr(op, *args):
    return {"op": op, "args": list(args)}


def lower(program):
    return lower_bundle(bundle(listed(program)), syntax="block-list-v2")["programs"][0]["program"]


class PureExpressionTests(unittest.TestCase):
    def value(self, program, inputs=None):
        return run_until_boundary(new_machine(lower(program), inputs)).value

    def test_nested_pure_data_and_original_source_unchanged(self):
        source = pure(
            {"total": expr("mul", expr("add", 17, 24), 3), "label": expr("concat", "a", "b")}
        )
        before = copy.deepcopy(source)
        self.assertEqual(self.value(source), {"total": 123, "label": "ab"})
        self.assertEqual(source, before)
        for a in (-8, 0, 9):
            for b in (-3, 0, 12):
                self.assertEqual(self.value(pure(expr("sub", expr("mul", a, b), a))), a * b - a)

    def test_explicit_ops_and_terminator_computation_keep_order(self):
        source = pure(expr("add", {"var": "n"}, 1))
        source["blocks"]["main"]["ops"] = [
            {"op": "add", "dest": "n", "args": [expr("mul", 2, 3), 4]}
        ]
        self.assertEqual(self.value(source), 11)
        self.assertEqual(
            [op["op"] for op in lower(source)["blocks"]["main"]["ops"]], ["mul", "add", "add"]
        )

    def test_fresh_register_cannot_capture_parameter_or_undefined_reference(self):
        source = pure(expr("add", {"var": "__flora_expr_0"}, 5), params=["__flora_expr_0"])
        program = lower(source)
        self.assertEqual(program["blocks"]["main"]["ops"][0]["dest"], "__flora_expr_1")
        self.assertEqual(self.value(source, {"__flora_expr_0": 8}), 13)
        bad = pure([expr("add", 2, 3), {"var": "__flora_expr_0"}])
        with self.assertRaisesRegex(ValidationError, "undefined variable"):
            lower(bad)

    def test_escaped_code_as_data_and_legacy_versions_unchanged(self):
        data = expr("effect", {"tool": "not_granted"})
        self.assertEqual(self.value(pure({"literal": data})), data)
        source = bundle(listed(pure(expr("add", 1, 2))))
        with self.assertRaisesRegex(ValidationError, "inline operation"):
            lower_bundle(source, syntax="block-list-v1")
        old = lower_bundle(bundle(pure(data)), syntax="observe-v1")
        self.assertEqual(run_until_boundary(new_machine(old["programs"][0]["program"])).value, data)

    def test_forbidden_inline_effect_unknown_op_wrong_arity_or_shape(self):
        for value in (
            expr("effect", {}),
            expr("call", {}),
            expr("eval", "1+1"),
            expr("add", 1),
            expr("get", {}, "k", "extra"),
            {"op": [], "args": []},
            {"op": "add", "args": "1+2"},
            {"op": "add", "args": [1, 2], "dest": "inlined"},
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                lower(pure(value))

    def test_branch_argument_names_are_not_expression_operators(self):
        source = pure(None)
        source["blocks"]["main"]["term"] = {
            "op": "branch",
            "condition": expr("eq", 2, 2),
            "yes": "end",
            "no": "end",
            "args": {"op": "a", "args": expr("add", 1, 2)},
        }
        source["blocks"]["end"] = {
            "params": ["op", "args"],
            "ops": [],
            "term": {"op": "return", "value": [{"var": "op"}, {"var": "args"}]},
        }
        self.assertEqual(self.value(source), ["a", 3])

    def test_observed_values_are_not_source_and_effect_occurs_once(self):
        calls = []

        def inventory(quantity: int):
            calls.append(quantity)
            return {"count": quantity, "data": expr("add", 10, 20)}

        source = {
            "version": 1,
            "entry": "main",
            "blocks": {
                "main": {
                    "params": [],
                    "ops": [],
                    "term": {
                        "op": "observe",
                        "tool": "inventory",
                        "args": {"quantity": expr("add", 2, 5)},
                        "capture": {"op": "kept", "args": expr("add", 1, 1)},
                        "bind": "v",
                        "success": "ok",
                        "error": "err",
                    },
                },
                "ok": {
                    "params": ["op", "args", "v"],
                    "ops": [],
                    "term": {
                        "op": "return",
                        "value": {
                            "count": expr("add", expr("get", {"var": "v"}, "count"), 1),
                            "data": expr("get", {"var": "v"}, "data"),
                            "captured": [{"var": "op"}, {"var": "args"}],
                        },
                    },
                },
                "err": {
                    "params": ["op", "args", "v"],
                    "ops": [],
                    "term": {"op": "return", "value": {"var": "v"}},
                },
            },
        }
        compiled = bundle(lower(source))
        self.assertEqual(calls, [])
        result = Runtime(make_registry([inventory])).run("read once", bundle=compiled)
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, [7])
        self.assertEqual(
            result.value, {"count": 8, "data": expr("add", 10, 20), "captured": ["kept", 2]}
        )

    def test_short_circuit_is_not_invented_and_operation_limit_remains(self):
        program = lower(pure(expr("and", False, expr("get", {}, "missing"))))
        self.assertEqual(run_until_boundary(new_machine(program)).kind, "fault")
        with self.assertRaisesRegex(ValidationError, "10000 operations"):
            lower(pure([expr("add", 1, 2)] * 10_001))

    def test_inputs_witnesses_forecasts_are_never_lowered(self):
        source = examples()[2]
        marker = expr("add", 2, 3)
        source["diagnostics"][0]["witnesses"] = [marker]
        source["programs"][0]["inputs"] = {"data": marker}
        result = lower_bundle(source, syntax="block-list-v2")
        self.assertEqual(result["diagnostics"][0]["witnesses"], [marker])
        self.assertEqual(result["programs"][0]["inputs"], {"data": marker})
        self.assertEqual(
            result["diagnostics"][0]["forecasts"], source["diagnostics"][0]["forecasts"]
        )

    def test_v2_compiler_still_validates_capabilities_and_known_arguments(self):
        provider = SequenceProvider(
            ModelResponse(json.dumps(bundle(listed(pure(expr("add", 2, 3))))), 1, 1)
        )
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v1")
        result = compiler.compile(context())
        self.assertEqual(run_until_boundary(new_machine(result["programs"][0]["program"])).value, 5)
        self.assertEqual(provider.calls, 1)
        prompt = compact_prompt("block-list-v2")
        self.assertIn("Pure expressions MAY be nested", prompt)
        self.assertNotIn("NOT an inline expression", prompt)
        self.assertIn("NOT an inline expression", compact_prompt("block-list-v1"))

    def test_known_tool_schema_errors_and_ungranted_tools_still_rejected(self):
        source = examples()[1]
        term = source["programs"][0]["program"]["blocks"]["main"]["term"]
        term["tool"] = "inventory"
        term["args"] = {"quantity": "wrong"}
        tools = [
            {
                "name": "inventory",
                "parameters": {
                    "type": "object",
                    "properties": {"quantity": {"type": "integer"}},
                    "required": ["quantity"],
                    "additionalProperties": False,
                },
            }
        ]
        for granted in ([], tools):
            provider = SequenceProvider(ModelResponse(json.dumps(source), 1, 1))
            compiler = LLMCompiler(
                provider, syntax="block-list-v2", prompt_style="compact-v1", max_repairs=0
            )
            with self.assertRaises(CompilerError):
                compiler.compile(context(tools=granted))
            self.assertEqual(provider.calls, 1)

    def test_existing_diagnostic_bundles_lower_to_identical_ir(self):
        for source in examples():
            self.assertEqual(
                lower_bundle(source, syntax="block-list-v2"),
                lower_bundle(source, syntax="block-list-v1"),
            )
