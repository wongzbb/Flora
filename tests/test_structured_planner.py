import json
import unittest

from flora.integrations.providers import ModelResponse, TransportError
from flora.integrations.binding import make_registry
from flora.engine.runtime import Runtime
from flora.language.compiler import LLMCompiler, _compile_structured_plan, _semantic_json_loads
from flora.state.trace import GENESIS
from flora.language.structured import lower_plan
from flora.language.vm import new_machine, resume, run_until_boundary
from flora.support.errors import ValidationError
from tests.helpers import context, bundle, pure


class PlannerProvider:
    def __init__(self, first, plan):
        self.responses = [first, plan]
        self.requests = []

    def complete(self, messages, *, max_tokens):
        self.requests.append(messages)
        return ModelResponse(json.dumps(self.responses.pop(0)), 1, 1)


class StructuredPlannerTests(unittest.TestCase):
    def test_semantic_stream_stall_gets_a_fresh_complete_phase(self):
        plan = {"steps": [], "return": {"literal": "recovered"}}

        class Provider:
            def __init__(self):
                self.calls = 0

            def complete(self, messages, *, max_tokens):
                self.calls += 1
                if self.calls == 1:
                    raise TransportError(
                        "partial semantic response discarded",
                        category="model_no_progress",
                        diagnostics={"reason": "output_idle_timeout"},
                    )
                return ModelResponse(json.dumps(plan), 1, 1)

        provider = Provider()
        compiler = LLMCompiler(
            provider,
            syntax="block-list-v3",
            prompt_style="compact-v3",
            semantic_first=True,
        )
        compiler.transport_retries = 0
        compiler_events = []
        compiler.on_event = compiler_events.append
        result = compiler.compile(context())
        entry = result["programs"][0]["program"]["entry"]
        self.assertEqual(
            result["programs"][0]["program"]["blocks"][entry]["term"]["value"],
            {"literal": "recovered"},
        )
        self.assertEqual(provider.calls, 2)
        self.assertTrue(
            any(event["kind"] == "structured_plan_transport_repair_requested" for event in compiler_events)
        )

    def test_semantic_boundary_repairs_one_missing_comma_only(self):
        plan, trailing, repair = _semantic_json_loads(
            '{"steps":[] "return":{"literal":"ok"}}'
        )
        self.assertEqual(plan, {"steps": [], "return": {"literal": "ok"}})
        self.assertEqual(trailing, 0)
        self.assertEqual(repair, "insert_missing_comma")

    def test_semantic_boundary_repairs_two_unique_missing_commas_without_guessing(self):
        plan, trailing, repair = _semantic_json_loads(
            '{"steps":[{"let":"x" "value":{"literal":1}}] "return":{"var":"x"}}'
        )
        self.assertEqual(plan["steps"][0]["value"], {"literal": 1})
        self.assertEqual(plan["return"], {"var": "x"})
        self.assertEqual(trailing, 0)
        self.assertEqual(repair, "insert_missing_commas:2")

    def test_get_default_projection_shorthand_uses_canonical_arity(self):
        program = lower_plan({
            "steps": [],
            "return": {
                "op": "get_default",
                "args": [
                    {"get": {"from": {"literal": {}}, "path": ["accepted"]}},
                    {"literal": False},
                ],
            },
        })
        boundary = run_until_boundary(
            new_machine(program), receipts=[{"status": "returned", "value": 4}]
        )
        self.assertEqual(boundary.value, False)

    def test_pure_receipt_read_call_sugar_never_becomes_an_effect(self):
        program = lower_plan({
            "steps": [{
                "call": "read_receipt",
                "args": {"index": {"literal": 0}},
                "save": "receipt",
            }],
            "return": {"var": "receipt"},
        })
        boundary = run_until_boundary(
            new_machine(program), receipts=[{"status": "returned", "value": 4}]
        )
        self.assertEqual(boundary.kind, "return")
        self.assertEqual(boundary.value["status"], "returned")

    def test_effect_call_without_save_is_rejected_with_observation_guidance(self):
        with self.assertRaisesRegex(ValidationError, "effect calls require save"):
            lower_plan({
                "steps": [{"call": "complete_task", "args": {}}],
                "return": {"literal": "done"},
            })

    def test_semantic_repair_exposes_missing_save_as_actionable_error(self):
        arguments = {
            "note": "done",
            "evidence": [],
            "expected_revision": 0,
        }
        invalid = {
            "steps": [{"call": "complete_task", "args": arguments}],
            "return": {"literal": "done"},
        }
        corrected = {
            "steps": [{"call": "complete_task", "args": arguments, "save": "completion"}],
            "return": {"var": "completion"},
        }
        provider = PlannerProvider(invalid, corrected)
        compiler_context = context(tools=[{
            "name": "complete_task",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "evidence": {"type": "array"},
                    "expected_revision": {"type": "integer"},
                },
                "required": ["note", "evidence", "expected_revision"],
                "additionalProperties": False,
            },
        }])
        compiler_context.reports = [
            {"kind": "completion_rejected", "details": {"pending_steps": ["task"]}}
        ]
        compiler = LLMCompiler(
            provider,
            syntax="block-list-v3",
            prompt_style="compact-v3",
        )
        result = compiler.compile(compiler_context)
        self.assertEqual(len(provider.requests), 2)
        self.assertIn("effect calls require save", provider.requests[1][1]["content"])
        self.assertIn("CORRECTION MODE", provider.requests[1][0]["content"])
        self.assertEqual(
            [
                block["term"].get("value")
                for block in result["programs"][0]["program"]["blocks"].values()
                if block["term"]["op"] == "return"
            ],
            [{"var": "completion"}],
        )

    def test_semantic_bundle_preserves_candidates_and_anchors(self):
        semantic = {
            "programs": [
                {"id": "main", "inputs": {}, "program": {"steps": [], "return": {"literal": 1}}},
                {"id": "fallback", "inputs": {}, "program": {"steps": [], "return": {"literal": 2}}},
            ],
            "incumbent": "main",
            "diagnostics": [],
            "expected_epoch": 0,
            "expected_digest": GENESIS,
        }
        result = _compile_structured_plan(semantic, context(), max_bytes=100000)
        self.assertEqual([item["id"] for item in result["programs"]], ["main", "fallback"])
        self.assertEqual(result["expected_epoch"], 0)
        self.assertEqual(result["expected_digest"], GENESIS)

    def test_semantic_bundle_keeps_diagnostics_and_revision_contracts(self):
        semantic_program = {"steps": [], "return": {"literal": 1}}
        semantic = {
            "programs": [{"id": "old", "inputs": {}, "program": semantic_program}],
            "incumbent": "old",
            "diagnostics": [{
                "id": "check_value",
                "inputs": {},
                "program": {
                    "steps": [{"call": "read_value", "args": {}, "save": "value"}],
                    "return": {"var": "value"},
                },
                "forecasts": [{
                    "candidate_id": "old",
                    "predicate": {"op": "eq", "path": [], "value": 1},
                }],
                "witnesses": [1],
            }],
            "revisions": [{
                "id": "old_preserved",
                "target_candidate": "old",
                "parameters": [],
                "program": semantic_program,
                "migration": {"steps": [], "return": {"var": "context"}},
                "mode": "PRESERVE",
            }],
            "expected_epoch": 0,
            "expected_digest": GENESIS,
        }
        compiler_context = context(tools=[{
            "name": "read_value",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }])
        compiler_context.previous_programs = [{"id": "old"}]
        result = _compile_structured_plan(semantic, compiler_context, max_bytes=100000)
        self.assertEqual(result["diagnostics"][0]["id"], "check_value")
        self.assertEqual(result["revisions"][0]["target_candidate"], "old")
        self.assertEqual(result["revisions"][0]["migration"]["blocks"][result["revisions"][0]["migration"]["entry"]]["params"], ["context"])

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

    def test_branch_join_merges_names_defined_on_both_paths(self):
        plan = {
            "steps": [
                {
                    "if": {"literal": True},
                    "then": [{"let": "final", "value": {"literal": 4}}],
                    "else": [{"let": "final", "value": {"literal": 0}}],
                }
            ],
            "return": {"var": "final"},
        }
        program = lower_plan(plan)
        boundary = run_until_boundary(new_machine(program))
        self.assertEqual(boundary.value, 4)

    def test_one_sided_semantic_guard_defaults_to_empty_else(self):
        program = lower_plan({
            "steps": [{
                "if": {"literal": True},
                "then": [{"let": "answer", "value": {"literal": 4}}],
            }],
            "return": {"literal": "guarded"},
        })
        self.assertEqual(run_until_boundary(new_machine(program)).value, "guarded")

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

    def test_pure_operation_arity_failure_uses_semantic_fallback(self):
        bad_program = pure(1)
        bad_program["blocks"]["main"]["ops"] = [{"dest": "bad", "op": "get", "args": [1]}]
        provider = PlannerProvider(
            bundle(bad_program), {"steps": [], "return": {"literal": "arity-recovered"}}
        )
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        result = compiler.compile(context())
        entry = result["programs"][0]["program"]["entry"]
        self.assertEqual(
            result["programs"][0]["program"]["blocks"][entry]["term"]["value"],
            {"literal": "arity-recovered"},
        )
        self.assertIn("semantic action planner", provider.requests[1][0]["content"])

    def test_completion_rejection_starts_semantic_phase(self):
        plan = {"steps": [], "return": {"literal": "bookkeeping"}}

        class Provider:
            def __init__(self):
                self.requests = []

            def complete(self, messages, *, max_tokens):
                self.requests.append(messages)
                return ModelResponse(json.dumps(plan), 1, 1)

        provider = Provider()
        compiler_context = context()
        compiler_context.reports = [
            {"kind": "completion_rejected", "details": {"pending_steps": ["task"]}}
        ]
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        compiler.compile(compiler_context)
        self.assertIn("semantic action planner", provider.requests[0][0]["content"])
        self.assertIn("Each compiled phase starts with an empty local scope", provider.requests[0][0]["content"])
        self.assertIn("bookkeeping tools such as complete_task", provider.requests[0][0]["content"])
        self.assertIn("Never emit a bare {\"call\":...} without save", provider.requests[0][0]["content"])

    def test_semantic_first_skips_low_level_control_flow_for_complex_profiles(self):
        plan = {"steps": [], "return": {"literal": "semantic-first"}}

        class Provider:
            def __init__(self):
                self.requests = []

            def complete(self, messages, *, max_tokens):
                self.requests.append(messages)
                return ModelResponse(json.dumps(plan), 1, 1)

        provider = Provider()
        compiler = LLMCompiler(
            provider,
            syntax="block-list-v3",
            prompt_style="compact-v3",
            semantic_first=True,
        )
        result = compiler.compile(context(tools=[{"name": "spawn_agents"}]))
        entry = result["programs"][0]["program"]["entry"]
        self.assertEqual(
            result["programs"][0]["program"]["blocks"][entry]["term"]["value"],
            {"literal": "semantic-first"},
        )
        self.assertIn("semantic action planner", provider.requests[0][0]["content"])

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

    def test_map_expression_is_compositional_inside_effect_arguments(self):
        plan = {
            "steps": [
                {
                    "call": "review_agents",
                    "args": {
                        "reviews": {
                            "for_each": ["index", "row"],
                            "in": {"literal": [{"agent_id": "a", "result_digest": "d"}]},
                            "yield": {
                                "agent_id": {
                                    "get": {"from": {"var": "row"}, "path": ["agent_id"]}
                                },
                                "result_digest": {
                                    "get": {"from": {"var": "row"}, "path": ["result_digest"]}
                                },
                            },
                        }
                    },
                    "save": "reviewed",
                }
            ],
            "return": {"var": "reviewed"},
        }
        first = run_until_boundary(new_machine(lower_plan(plan)))
        self.assertEqual(first.kind, "effect")
        self.assertEqual(
            first.request,
            {
                "tool": "review_agents",
                "args": {"reviews": [{"agent_id": "a", "result_digest": "d"}]},
            },
        )

    def test_nested_terminal_handler_is_an_explicit_empty_phase(self):
        plan = {
            "steps": [
                {
                    "call": "read_value",
                    "args": {},
                    "save": "value",
                    "on_error": {
                        "save": "failure",
                        "plan": {
                            "replan": {
                                "reason": "Use the observed failure",
                                "state": {"error": {"var": "failure"}},
                            }
                        },
                    },
                }
            ],
            "return": {"var": "value"},
        }
        first = run_until_boundary(new_machine(lower_plan(plan)))
        failed = run_until_boundary(
            resume(
                first.machine,
                {"status": "raised", "error": {"type": "read_error", "message": "offline"}},
            )
        )
        self.assertEqual(failed.kind, "replan")
        self.assertEqual(failed.value["state"]["error"]["message"], "offline")


if __name__ == "__main__":
    unittest.main()
