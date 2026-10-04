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

    def test_semantic_phase_has_independent_bounded_correction(self):
        bad = bundle(pure(1))
        bad["programs"][0]["program"]["blocks"]["main"]["term"] = {
            "op": "jump", "target": "main", "args": {"unexpected": 1}
        }
        invalid_semantic = {"steps": [], "return": {"op": "not-a-real-op", "args": []}}
        provider = PlannerProvider(bad, invalid_semantic)
        provider.responses.append({"steps": [], "return": {"literal": "recovered"}})
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = compiler.compile(context())
        entry = result["programs"][0]["program"]["entry"]
        self.assertEqual(result["programs"][0]["program"]["blocks"][entry]["term"]["value"], {"literal": "recovered"})
        self.assertEqual(len(provider.requests), 3)

    def test_semantic_plan_keeps_first_object_when_relay_appends_notice(self):
        bad = "{" + (" " * 1200)
        plan = json.dumps({"steps": [], "return": {"literal": "ok"}})
        class RawProvider:
            def __init__(self):
                self.responses = [bad, plan + "\n<provider-notice>ignored</provider-notice>"]
                self.requests = []

            def complete(self, messages, *, max_tokens):
                self.requests.append(messages)
                return ModelResponse(self.responses.pop(0), 1, 1)

        provider = RawProvider()
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = compiler.compile(context())
        self.assertEqual(
            result["programs"][0]["program"]["blocks"][result["programs"][0]["program"]["entry"]]["term"]["value"],
            {"literal": "ok"},
        )

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

    def test_semantic_get_projection_and_save_only_error_are_host_lowered(self):
        plan = {
            "steps": [
                {
                    "call": "read_value",
                    "args": {},
                    "save": "value",
                    "on_error": {"save": "failure"},
                },
            ],
            "return": {"get": {"from": {"var": "value"}, "path": ["answer"]}},
        }
        program = lower_plan(plan)
        self.assertTrue(any(block["term"]["op"] == "replan" for block in program["blocks"].values()))
        self.assertTrue(any(
            operation["op"] == "get"
            for block in program["blocks"].values()
            for operation in block["ops"]
        ))

    def test_no_argument_call_defaults_to_empty_object(self):
        plan = {
            "steps": [{"call": "read_value", "save": "value"}],
            "return": {"var": "value"},
        }
        program = lower_plan(plan)
        self.assertTrue(any(block["term"].get("tool") == "read_value" for block in program["blocks"].values()))

    def test_mapped_return_exposes_index_and_value_and_collects_results(self):
        plan = {
            "steps": [],
            "return": {
                "for_each": ["index", "value"],
                "in": {"literal": [2, 4, 6]},
                "yield": {"op": "add", "args": [{"var": "value"}, 1]},
            },
        }
        program = lower_plan(plan)
        boundary = run_until_boundary(new_machine(program))
        self.assertEqual(boundary.kind, "return")
        self.assertEqual(boundary.value, [3, 5, 7])

    def test_map_expression_can_be_assigned_before_return(self):
        plan = {
            "steps": [
                {
                    "let": "questions",
                    "value": {
                        "for_each": ["index", "value"],
                        "in": {"literal": [1, 2]},
                        "yield": {"op": "mul", "args": [{"var": "value"}, 2]},
                    },
                },
            ],
            "return": {"questions": {"var": "questions"}},
        }
        boundary = run_until_boundary(new_machine(lower_plan(plan)))
        self.assertEqual(boundary.value, {"questions": [2, 4]})


if __name__ == "__main__":
    unittest.main()
