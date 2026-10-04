import json
import unittest

from flora.integrations.providers import ModelResponse
from flora.integrations.binding import make_registry
from flora.engine.runtime import Runtime
from flora.language.compiler import LLMCompiler
from flora.language.structured import lower_plan
from flora.language.vm import new_machine, run_until_boundary
from tests.helpers import context, bundle, pure


class PlannerProvider:
    def __init__(self, first, plan):
        self.responses = [first, plan]
        self.requests = []

    def complete(self, messages, *, max_tokens):
        self.requests.append(messages)
        return ModelResponse(json.dumps(self.responses.pop(0)), 1, 1)


class StructuredPlannerTests(unittest.TestCase):
    def test_host_builds_continuations_from_semantic_actions(self):
        plan = {
            "steps": [
                {"call": "read_value", "args": {}, "save": "value"},
                {"call": "read_next", "args": {"value": {"var": "value"}}, "save": "next"},
            ],
            "return": {"var": "next"},
        }
        program = lower_plan(plan)
        self.assertGreaterEqual(len(program["blocks"]), 7)
        self.assertEqual(program["blocks"][program["entry"]]["params"], [])
        self.assertTrue(all("resume" not in block["params"] for block in program["blocks"].values()))

    def test_effect_error_replans_with_actual_outcome(self):
        plan = {
            "steps": [{"call": "read_value", "args": {}, "save": "value"}],
            "replan": {"reason": "Decide from the observed value", "state": {"value": {"var": "value"}}},
        }
        program = lower_plan(plan)
        self.assertTrue(any(block["term"]["op"] == "replan" for block in program["blocks"].values()))

    def test_continuation_failure_uses_semantic_fallback(self):
        bad = bundle(pure(1))
        bad["programs"][0]["program"]["blocks"]["main"]["term"] = {
            "op": "jump", "target": "main", "args": {"unexpected": 1}
        }
        plan = {"steps": [{"call": "read_value", "args": {}, "save": "value"}], "return": {"var": "value"}}
        provider = PlannerProvider(bad, plan)
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = compiler.compile(context(tools=[{"name": "read_value", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]))
        self.assertIn(result["programs"][0]["program"]["entry"], result["programs"][0]["program"]["blocks"])
        self.assertIn("semantic action planner", provider.requests[1][0]["content"])

    def test_large_malformed_bundle_uses_semantic_fallback(self):
        provider = PlannerProvider("{" + (" " * 1200), {"steps": [], "return": {"literal": "ok"}})
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = compiler.compile(context())
        self.assertEqual(result["programs"][0]["program"]["blocks"][result["programs"][0]["program"]["entry"]]["term"]["value"], {"literal": "ok"})
        self.assertIn("semantic action planner", provider.requests[1][0]["content"])

    def test_planner_never_executes_effects(self):
        plan = {"steps": [], "return": {"literal": {"answer": 3}}}
        program = lower_plan(plan)
        self.assertEqual(run_until_boundary(new_machine(program)).value, {"answer": 3})

    def test_lowered_plan_executes_each_real_effect_once(self):
        calls = []

        def read_value():
            calls.append("read_value")
            return 3

        def read_next(value: int):
            calls.append(("read_next", value))
            return value + 1

        plan = {
            "steps": [
                {"call": "read_value", "args": {}, "save": "value"},
                {"call": "read_next", "args": {"value": {"var": "value"}}, "save": "answer"},
            ],
            "return": {"var": "answer"},
        }
        bad = bundle(pure(1))
        bad["programs"][0]["program"]["blocks"]["main"]["term"] = {
            "op": "jump", "target": "main", "args": {"unexpected": 1}
        }
        provider = PlannerProvider(bad, plan)
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = Runtime(make_registry([read_value, read_next]), compiler=compiler).run("run the phase")
        self.assertEqual((result.status, result.value), ("completed", 4))
        self.assertEqual(calls, ["read_value", ("read_next", 3)])


if __name__ == "__main__":
    unittest.main()
