# SPDX-License-Identifier: Apache-2.0
"""Optional model review preserves literal semantics, accounting and capabilities."""

import copy
import json
import unittest

from flora.engine.budget import Budget, BudgetLimits
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler
from flora.language.vm import new_machine, run_until_boundary
from flora.support.errors import BudgetExceeded, CompilerError, StaleAnchor
from tests.helpers import bundle, context, pure
from tests.test_toolcheck import TOOLS, authored


def quoted_bundle(*, indirect=False, corrected=False):
    payload = {"observed": {"var": "x"}}
    expression = payload if corrected else {"literal": payload}
    program = pure(expression)
    block = program["blocks"]["main"]
    block["ops"] = [{"op": "const", "dest": "x", "args": [7]}]
    if indirect:
        block["ops"].append({"op": "set", "dest": "out", "args": [{}, "project", expression]})
        block["term"]["value"] = {"var": "out"}
    return bundle(program)


class RecordingProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, *, max_tokens):
        self.requests.append(copy.deepcopy(messages))
        return ModelResponse(json.dumps(next(self.responses)), 3, 2)


class CompilerAdvisoryTests(unittest.TestCase):
    def compile_sequence(self, *responses, ctx=None, **options):
        provider = RecordingProvider(*responses)
        budget = Budget(BudgetLimits(max_model_calls=options.pop("model_calls", 2)))
        compiler = LLMCompiler(
            provider,
            before_call=budget.before_model_call,
            on_usage=budget.record_model_usage,
            **options,
        )
        events = []
        compiler.on_event = events.append
        return compiler, provider, budget, events, ctx or context()

    def test_intentional_literal_can_be_retained_unchanged(self):
        original = quoted_bundle()
        before = copy.deepcopy(original)
        compiler, provider, budget, events, ctx = self.compile_sequence(original, original)
        result = compiler.compile(ctx)
        self.assertEqual(result, before)
        self.assertEqual(original, before)
        actual = run_until_boundary(new_machine(result["programs"][0]["program"]))
        self.assertEqual(actual.value, {"observed": {"var": "x"}})
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(budget.model_calls, 2)
        self.assertEqual((budget.input_tokens, budget.output_tokens), (6, 4))
        self.assertEqual(sum(e["kind"] == "compiler_advisory" for e in events), 1)
        self.assertFalse(any(e["kind"] == "compiler_rejected" for e in events))
        self.assertEqual(provider.requests[0], provider.requests[1][:2])
        review = json.loads(provider.requests[1][-1]["content"])
        self.assertIn("valid IR", review["review"])
        self.assertNotIn("validation_error", review)

    def test_model_can_correct_direct_and_set_carried_literal(self):
        for indirect in (False, True):
            with self.subTest(indirect=indirect):
                original = quoted_bundle(indirect=indirect)
                corrected = quoted_bundle(indirect=indirect, corrected=True)
                compiler, provider, _, events, ctx = self.compile_sequence(original, corrected)
                result = compiler.compile(ctx)
                self.assertEqual(result, corrected)
                actual = run_until_boundary(new_machine(result["programs"][0]["program"]))
                expected = {"observed": 7}
                self.assertEqual(actual.value, {"project": expected} if indirect else expected)
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(sum(e["kind"] == "compiler_advisory" for e in events), 1)

    def test_zero_repairs_accepts_valid_literal_without_review(self):
        original = quoted_bundle()
        compiler, provider, _, events, ctx = self.compile_sequence(original, max_repairs=0)
        self.assertEqual(compiler.compile(ctx), original)
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(any(e["kind"] == "compiler_advisory" for e in events))

    def test_literal_without_a_local_register_reference_needs_no_review(self):
        source = bundle(pure({"literal": {"observed": {"var": "not_a_local_register"}}}))
        compiler, provider, _, events, ctx = self.compile_sequence(source)
        self.assertEqual(compiler.compile(ctx), source)
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(any(e["kind"] == "compiler_advisory" for e in events))

    def test_intentional_literal_in_set_can_survive_control_flow_unchanged(self):
        source = quoted_bundle(indirect=True)
        blocks = source["programs"][0]["program"]["blocks"]
        blocks["main"]["term"] = {
            "op": "jump",
            "target": "done",
            "args": {"result": {"var": "out"}},
        }
        blocks["done"] = {
            "params": ["result"],
            "ops": [],
            "term": {"op": "return", "value": {"var": "result"}},
        }
        compiler, provider, _, _, ctx = self.compile_sequence(source, source)
        result = compiler.compile(ctx)
        self.assertEqual(result, source)
        self.assertEqual(len(provider.requests), 2)
        actual = run_until_boundary(new_machine(result["programs"][0]["program"]))
        self.assertEqual(actual.value, {"project": {"observed": {"var": "x"}}})

    def test_review_uses_existing_budget_and_never_bypasses_exhaustion(self):
        original = quoted_bundle()
        compiler, provider, budget, _, ctx = self.compile_sequence(original, model_calls=1)
        with self.assertRaises(BudgetExceeded):
            compiler.compile(ctx)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(budget.model_calls, 1)
        self.assertEqual((budget.input_tokens, budget.output_tokens), (3, 2))

    def test_review_cannot_change_anchor_or_add_tool_capabilities(self):
        stale = quoted_bundle(corrected=True)
        stale["expected_epoch"] = 1
        unauthorized = authored({"account": "a", "quantity": 2})
        for response, error in ((stale, StaleAnchor), (unauthorized, CompilerError)):
            with self.subTest(error=error.__name__):
                compiler, provider, budget, _, ctx = self.compile_sequence(
                    quoted_bundle(), response
                )
                with self.assertRaises(error):
                    compiler.compile(ctx)
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual(budget.model_calls, 2)

    def test_real_schema_contradiction_still_uses_validation_repair(self):
        bad = authored({"account": {"literal": {"var": "x"}}, "quantity": 2})
        good = authored({"account": "a", "quantity": 2})
        compiler, provider, _, events, ctx = self.compile_sequence(
            bad, good, ctx=context(TOOLS), syntax="block-list-v1", prompt_style="compact-v1"
        )
        self.assertEqual(compiler.compile(ctx), good)
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(any(e["kind"] == "compiler_rejected" for e in events))
        self.assertFalse(any(e["kind"] == "compiler_advisory" for e in events))

    def test_validation_repair_requests_a_minimal_next_effect_phase(self):
        bad = authored({"account": {"literal": {"var": "x"}}, "quantity": 2})
        good = authored({"account": "a", "quantity": 2})
        compiler, provider, _, _, ctx = self.compile_sequence(
            bad, good, ctx=context(TOOLS), syntax="block-list-v1", prompt_style="compact-v1"
        )
        self.assertEqual(compiler.compile(ctx), good)
        repair = json.loads(provider.requests[1][-1]["content"])
        self.assertIn("next necessary effect", repair["guidance"])
        self.assertIn("Never replay an already successful effect", repair["guidance"])

    def test_bundle_envelope_repair_preserves_required_protocol_fields(self):
        bad = {"revisions": []}
        good = quoted_bundle()
        compiler, provider, _, events, ctx = self.compile_sequence(bad, good)
        self.assertEqual(compiler.compile(ctx), good)
        self.assertTrue(any(e["kind"] == "compiler_rejected" for e in events))
        repair = json.loads(provider.requests[1][-1]["content"])
        self.assertIn("programs", repair["guidance"])
        self.assertIn("expected_digest", repair["guidance"])
        self.assertIn("revisions is optional", repair["guidance"])

    def test_unknown_dynamic_tool_argument_is_not_rejected_or_guessed(self):
        source = authored({"account": {"var": "account"}, "quantity": 2})
        source["programs"][0]["program"]["blocks"]["main"]["ops"] = [
            {"op": "read_memory", "dest": "account", "args": []}
        ]
        compiler, provider, _, events, ctx = self.compile_sequence(
            source, ctx=context(TOOLS), syntax="block-list-v1", prompt_style="compact-v1"
        )
        self.assertEqual(compiler.compile(ctx), source)
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(
            any(e["kind"] in {"compiler_rejected", "compiler_advisory"} for e in events)
        )


if __name__ == "__main__":
    unittest.main()
