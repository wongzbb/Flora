# SPDX-License-Identifier: Apache-2.0
"""Generic schema checks work for arbitrary tool names, not benchmark prompts."""

import copy
import json
import unittest

from flora.integrations.providers import ModelResponse
from flora.integrations.tools import validate_schema
from flora.language.compiler import LLMCompiler
from flora.language.toolcheck import validate_effect_arguments
from flora.support.errors import CompilerError, ValidationError
from tests.helpers import bundle, context, pure
from tests.test_recovery import SequenceProvider

SCHEMA = {
    "type": "object",
    "properties": {
        "account": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1, "maximum": 12},
        "mode": {"enum": ["reserve", "confirm"]},
        "meta": {"type": "object", "required": ["tag"], "properties": {"tag": {"type": "string"}}},
        "labels": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
    },
    "required": ["account", "quantity"],
    "additionalProperties": False,
}
TOOLS = [{"name": "reserve_inventory", "parameters": SCHEMA}]


def authored(args):
    program = {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": {
                "params": [],
                "ops": [],
                "term": {
                    "op": "effect",
                    "tool": "reserve_inventory",
                    "args": args,
                    "bind": "reply",
                    "capture": {},
                    "resume": "done",
                },
            },
            "done": {
                "params": ["reply"],
                "ops": [],
                "term": {"op": "return", "value": {"var": "reply"}},
            },
        },
    }
    return bundle(program)


class ToolCheckTests(unittest.TestCase):
    def test_known_contradictions_with_precise_locations(self):
        for args in (
            {"account": "a"},
            {"account": "a", "quantity": True},
            {"account": "", "quantity": 1},
            {"account": "a", "quantity": 13},
            {"account": "a", "quantity": 2, "mode": "delete"},
            {"account": "a", "quantity": 2, "invented": {"var": "v"}},
            {"account": {"var": "v"}},
            {"account": {"var": "v"}, "quantity": 2, "meta": {"other": {"var": "x"}}},
            {"account": {"var": "v"}, "quantity": 2, "labels": [{"var": "x"}, 7]},
            {"account": {"var": "v"}, "quantity": 2, "labels": [{"var": "x"}] * 3},
        ):
            with (
                self.subTest(args=args),
                self.assertRaisesRegex(
                    ValidationError, r"programs main, block main, tool reserve_inventory:"
                ),
            ):
                validate_effect_arguments(authored(args), TOOLS)

    def test_dynamic_values_and_whole_requests_are_deferred_not_guessed(self):
        for args in (
            {"var": "request"},
            {"account": {"var": "a"}, "quantity": {"var": "n"}},
            {"account": "a", "quantity": 2, "meta": {"tag": {"var": "tag"}}},
            {"account": "a", "quantity": 2, "labels": ["one", {"var": "tag"}]},
        ):
            value = authored(args)
            before = copy.deepcopy(value)
            validate_effect_arguments(value, TOOLS)
            self.assertEqual(value, before)

    def test_escaped_literals_are_data_not_variables(self):
        with self.assertRaises(ValidationError):
            validate_effect_arguments(
                authored({"account": {"literal": {"var": "x"}}, "quantity": 2}), TOOLS
            )
        validate_effect_arguments(authored({"literal": {"account": "a", "quantity": 2}}), TOOLS)

    def test_nested_additional_properties_schema_and_partial_enum(self):
        tools = [
            {
                "name": "reserve_inventory",
                "parameters": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "enum": [{"x": 1}],
                },
            }
        ]
        validate_effect_arguments(authored({"x": {"var": "v"}}), tools)
        with self.assertRaises(ValidationError):
            validate_effect_arguments(authored({"x": {"var": "v"}, "y": "bad"}), tools)
        with self.assertRaises(ValidationError):
            validate_effect_arguments(authored({"x": 2}), tools)

    def test_opaque_slots_defer_to_live_store_and_do_not_mutate_literals(self):
        tools = [dict(TOOLS[0], opaque_parameters=["account"])]
        value = authored({"literal": {"account": {"opaque": "carrier"}, "quantity": 2}})
        before = copy.deepcopy(value)
        validate_effect_arguments(value, tools)
        self.assertEqual(value, before)
        with self.assertRaises(ValidationError):
            validate_effect_arguments(authored({"account": {"var": "v"}}), tools)

    def test_diagnostics_and_revisions_are_checked_too(self):
        for kind in ("diagnostics", "revisions"):
            value = bundle(pure("ok"))
            value[kind] = [authored({"account": "a"})["programs"][0]]
            with self.assertRaisesRegex(ValidationError, kind):
                validate_effect_arguments(value, TOOLS)

    def test_aggregates_independent_locations_across_all_program_collections(self):
        value = authored({"account": "a"})
        normal = value["programs"][0]
        normal["program"]["blocks"]["second"] = copy.deepcopy(normal["program"]["blocks"]["main"])
        for kind in ("diagnostics", "revisions"):
            value[kind] = [dict(copy.deepcopy(normal), id=kind + "_id")]
        before = copy.deepcopy(value)
        with self.assertRaises(ValidationError) as caught:
            validate_effect_arguments(value, TOOLS)
        message = str(caught.exception)
        for kind in ("programs", "diagnostics", "revisions"):
            for label in ("main", "second"):
                self.assertRegex(message, f"{kind} [^\n]+, block {label}, tool reserve_inventory")
        self.assertEqual(len(message.splitlines()), 6)
        self.assertEqual(value, before)

    def test_tool_schema_diagnostics_are_bounded(self):
        value = authored({"account": "a"})
        blocks = value["programs"][0]["program"]["blocks"]
        for i in range(24):
            blocks[f"reserve_{i}"] = copy.deepcopy(blocks["main"])
        with self.assertRaises(ValidationError) as caught:
            validate_effect_arguments(value, TOOLS)
        self.assertEqual(len(str(caught.exception).splitlines()), 16)

    def test_compiler_repairs_before_any_effect_and_preserves_anchor(self):
        bad, good = authored({"account": "a"}), authored({"account": "a", "quantity": 2})
        ctx = context(tools=TOOLS)
        provider = SequenceProvider(
            ModelResponse(json.dumps(bad), 1, 1), ModelResponse(json.dumps(good), 1, 1)
        )
        calls, events = [], []
        compiler = LLMCompiler(
            provider, syntax="block-list-v1", prompt_style="compact-v1", before_call=calls.append
        )
        compiler.on_event = events.append
        self.assertEqual(compiler.compile(ctx), good)
        self.assertEqual(len(calls), 2)
        self.assertEqual(provider.calls, 2)
        rejection = next(e for e in events if e["kind"] == "compiler_rejected")
        self.assertIn("Required tool argument missing", rejection["message"])

    def test_partial_checks_do_not_reject_valid_runtime_instantiations(self):
        # Differential check against the real schema validator, not a second
        # handwritten oracle. Unknown data must never be treated as known wrong.
        choices = [None, False, 0, 1, 2, 13, "", "a", "reserve", [], {}, ["x"]]
        templates = [
            {"account": {"var": "v"}, "quantity": 2},
            {"account": "a", "quantity": {"var": "v"}},
            {"account": "a", "quantity": 2, "mode": {"var": "v"}},
            {"account": "a", "quantity": 2, "meta": {"tag": {"var": "v"}}},
            {"account": "a", "quantity": 2, "labels": [{"var": "v"}]},
        ]

        def instantiate(expression, replacement):
            if isinstance(expression, dict):
                if expression == {"var": "v"}:
                    return replacement
                return {key: instantiate(value, replacement) for key, value in expression.items()}
            if isinstance(expression, list):
                return [instantiate(value, replacement) for value in expression]
            return expression

        accepted = 0
        for template in templates:
            for replacement in choices:
                concrete = instantiate(template, replacement)
                try:
                    validate_schema(concrete, SCHEMA)
                except ValidationError:
                    continue
                accepted += 1
                validate_effect_arguments(authored(template), TOOLS)
        self.assertGreater(accepted, 10)

    def test_legacy_ir_compilation_is_unchanged(self):
        bad = authored({"account": "a"})
        provider = SequenceProvider(ModelResponse(json.dumps(bad), 1, 1))
        self.assertEqual(LLMCompiler(provider).compile(context(tools=TOOLS)), bad)
        provider = SequenceProvider(ModelResponse(json.dumps(bad), 1, 1))
        with self.assertRaises(CompilerError):
            LLMCompiler(
                provider, syntax="block-list-v1", prompt_style="compact-v1", max_repairs=0
            ).compile(context(tools=TOOLS))
