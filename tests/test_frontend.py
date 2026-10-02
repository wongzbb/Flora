# SPDX-License-Identifier: Apache-2.0
import copy
import json
import unittest
from pathlib import Path

from flora.checks.diagnostics import evaluate_diagnostic
from flora.engine.runtime import Runtime
from flora.examples import CalendarWorld, PaginationWorld, calendar_bundle, pagination_bundle
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler, validate_bundle
from flora.language.frontend import lower_bundle, lower_program
from flora.language.ir import parse_program
from flora.language.vm import new_machine, resume, run_until_boundary
from flora.support.errors import CompilerError, StaleAnchor, ValidationError
from tests.helpers import block, bundle, context, pure


def observe(tool="read_value", *, capture=None):
    captured = {} if capture is None else capture
    params = list(captured) + ["result"]
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(
                term={
                    "op": "observe",
                    "tool": tool,
                    "args": {},
                    "bind": "result",
                    "capture": captured,
                    "success": "ok",
                    "error": "err",
                }
            ),
            "ok": block(params, term={"op": "return", "value": {"var": "result"}}),
            "err": block(params, term={"op": "return", "value": {"error": {"var": "result"}}}),
        },
    }


def example_as_observe(program):
    """Hand-authored examples all start consumers by unwrapping reply.value."""
    program = copy.deepcopy(program)
    blocks = program["blocks"]
    consumers = {}
    for name, b in list(blocks.items()):
        t = b["term"]
        if t["op"] != "effect":
            continue
        target = t["resume"]
        consumer = blocks[target]
        if target not in consumers:
            operation = consumer["ops"].pop(0)
            assert operation["op"] == "get" and operation["args"] == [{"var": t["bind"]}, "value"]
            consumers[target] = operation["dest"]
            consumer["params"] = [
                operation["dest"] if p == t["bind"] else p for p in consumer["params"]
            ]
        bound = consumers[target]
        error = "error_" + name
        blocks[error] = block(
            list(t["capture"]) + [bound],
            term={"op": "replan", "reason": "Interpret actual tool error", "state": {"var": bound}},
        )
        b["term"] = {
            "op": "observe",
            "tool": t["tool"],
            "args": t["args"],
            "bind": bound,
            "capture": t["capture"],
            "success": target,
            "error": error,
        }
    return program


class FrontendTests(unittest.TestCase):
    def boundary(self, program, outcome):
        waiting = run_until_boundary(new_machine(lower_program(program)))
        self.assertEqual(waiting.kind, "effect")
        return run_until_boundary(resume(waiting.machine, outcome))

    def test_raw_success_error_and_unknown(self):
        result = self.boundary(observe(), {"status": "returned", "value": {"n": 9}})
        self.assertEqual((result.kind, result.value), ("return", {"n": 9}))
        error = {"type": "ValueError", "message": "fixture"}
        result = self.boundary(observe(), {"status": "raised", "error": error})
        self.assertEqual((result.kind, result.value), ("return", {"error": error}))
        result = self.boundary(observe(), {"status": "interrupted_unknown", "error": error})
        self.assertEqual(result.kind, "unknown")

    def test_capture_pure_consumers_and_branch_before_next_effect(self):
        p = observe(capture={"base": 10})
        p["blocks"]["ok"] = block(
            ["base", "result"],
            [
                {"op": "get", "dest": "n", "args": [{"var": "result"}, "n"]},
                {"op": "add", "dest": "sum", "args": [{"var": "n"}, {"var": "base"}]},
                {"op": "gt", "dest": "positive", "args": [{"var": "sum"}, 0]},
            ],
            {
                "op": "branch",
                "condition": {"var": "positive"},
                "yes": "next",
                "no": "zero",
                "args": {"sum": {"var": "sum"}},
            },
        )
        p["blocks"]["next"] = block(
            ["sum"],
            term={
                "op": "observe",
                "tool": "write_value",
                "args": {"n": {"var": "sum"}},
                "capture": {},
                "bind": "r",
                "success": "done",
                "error": "failed",
            },
        )
        p["blocks"]["zero"] = block(["sum"], term={"op": "return", "value": "nonpositive"})
        p["blocks"]["done"] = block(["r"], term={"op": "return", "value": {"var": "r"}})
        p["blocks"]["failed"] = block(
            ["r"], term={"op": "replan", "reason": "Interpret error", "state": {"var": "r"}}
        )
        b = self.boundary(p, {"status": "returned", "value": {"n": 2}})
        self.assertEqual(b.request, {"tool": "write_value", "args": {"n": 12}})
        b = run_until_boundary(resume(b.machine, {"status": "returned", "value": "saved"}))
        self.assertEqual((b.kind, b.value), ("return", "saved"))
        b = self.boundary(p, {"status": "returned", "value": {"n": -11}})
        self.assertEqual((b.kind, b.value), ("return", "nonpositive"))

    def test_missing_data_stays_a_fault_not_fabricated_success(self):
        p = observe()
        p["blocks"]["ok"]["ops"] = [{"op": "get", "dest": "n", "args": [{"var": "result"}, "n"]}]
        self.assertEqual(self.boundary(p, {"status": "returned", "value": {}}).kind, "fault")

    def test_replan_is_only_explicit(self):
        p = observe()
        p["blocks"]["ok"]["term"] = {
            "op": "replan",
            "reason": "Interpret",
            "state": {"var": "result"},
        }
        b = self.boundary(p, {"status": "returned", "value": {"actual": 7}})
        self.assertEqual(b.kind, "replan")
        self.assertEqual(b.value["state"], {"actual": 7})

    def test_fresh_names_deterministic_and_no_mutation(self):
        p = observe(capture={"__flora_observe_0_reply": 3})
        p["blocks"]["__flora_observe_1_ok"] = block(term={"op": "return", "value": 1})
        before = copy.deepcopy(p)
        expanded = lower_program(p)
        self.assertEqual(p, before)
        self.assertIn("__flora_observe_2_check", expanded["blocks"])
        self.assertEqual(expanded, lower_program(p))
        self.assertEqual(expanded, lower_program(expanded))

    def test_literals_never_rewritten(self):
        literal = {"op": "observe", "blocks": {"main": {"term": {"op": "observe"}}}}
        p = pure({"literal": literal})
        self.assertEqual(lower_program(p), p)
        source = bundle(p)
        source["programs"][0]["inputs"] = {"data": literal}
        self.assertEqual(lower_bundle(source)["programs"][0]["inputs"]["data"], literal)

    def test_branches_and_bounds_required(self):
        for field, value in [
            ("success", "missing"),
            ("bind", "bad name"),
            ("capture", {"result": 1}),
            ("capture", []),
            ("tool", ""),
        ]:
            with self.subTest(field=field):
                p = observe()
                p["blocks"]["main"]["term"][field] = value
                with self.assertRaises(ValidationError):
                    lower_program(p)
        p = observe()
        del p["blocks"]["main"]["term"]["error"]
        with self.assertRaises(ValidationError):
            lower_program(p)
        p = observe()
        p["blocks"]["err"]["params"] = []
        with self.assertRaises(ValidationError):
            lower_program(p)
        p = observe()
        p["blocks"].update({f"b{i}": block(term={"op": "return", "value": 0}) for i in range(508)})
        with self.assertRaisesRegex(ValidationError, "512"):
            lower_program(p)

    def test_ir_mode_still_rejects_new_syntax(self):
        with self.assertRaises(ValidationError):
            parse_program(observe())

        class Provider:
            def complete(self, messages, *, max_tokens):
                return ModelResponse(json.dumps(bundle(observe())))

        with self.assertRaises(CompilerError):
            LLMCompiler(Provider(), max_repairs=0).compile(context([{"name": "read_value"}]))
        result = LLMCompiler(Provider(), syntax="observe-v1").compile(
            context([{"name": "read_value"}])
        )
        self.assertEqual(result["programs"][0]["program"]["blocks"]["main"]["term"]["op"], "effect")

    def test_block_list_normalizes_only_unambiguous_control_metadata(self):
        source = bundle(
            [
                {
                    "label": "main",
                    "params": [],
                    "ops": [],
                    "term": {"op": "effect", "tool": "read_value", "args": {}},
                    "resume": "after",
                    "bind": "reply",
                    "capture": {},
                },
                {
                    "label": "after",
                    "params": ["reply"],
                    "ops": [],
                    "term": {"op": "return", "value": {"var": "reply"}},
                },
            ]
        )
        lowered = lower_bundle(source, syntax="block-list-v3")
        term = lowered["programs"][0]["program"]["blocks"]["main"]["term"]
        self.assertEqual((term["op"], term["resume"], term["bind"]), ("effect", "after", "reply"))
        ambiguous = copy.deepcopy(source)
        ambiguous["programs"][0]["program"][0]["resume"] = "other"
        ambiguous["programs"][0]["program"][0]["term"]["resume"] = "after"
        with self.assertRaises(ValidationError):
            lower_bundle(ambiguous, syntax="block-list-v3")

    def test_block_list_normalizes_effect_with_complete_observe_outcomes(self):
        source = bundle(
            [
                {
                    "label": "main",
                    "params": [],
                    "ops": [],
                    "term": {
                        "op": "effect",
                        "tool": "read_value",
                        "args": {},
                        "bind": "value",
                        "capture": {},
                        "success": "ok",
                        "error": "err",
                    },
                },
                {"label": "ok", "params": ["value"], "ops": [], "term": {"op": "return", "value": {"var": "value"}}},
                {"label": "err", "params": ["value"], "ops": [], "term": {"op": "return", "value": {"var": "value"}}},
            ]
        )
        lowered = lower_bundle(source, syntax="block-list-v3")
        self.assertEqual(lowered["programs"][0]["program"]["blocks"]["main"]["term"]["op"], "effect")

    def test_capabilities_anchors_and_migration_purity_not_bypassed(self):
        expanded = lower_bundle(bundle(observe()))
        with self.assertRaises(ValidationError):
            validate_bundle(expanded, allowed_tools=[])
        with self.assertRaises(StaleAnchor):
            validate_bundle(expanded, expected_epoch=9)
        source = bundle(pure(3))
        source["revisions"] = [
            {
                "id": "change",
                "target_candidate": "old",
                "program": observe(),
                "migration": observe(),
                "mode": "CHANGE",
            }
        ]
        with self.assertRaises(ValidationError):
            validate_bundle(lower_bundle(source), allowed_tools=["read_value"])
        source["revisions"][0]["migration"] = pure({}, ["context"])
        validated = validate_bundle(lower_bundle(source), allowed_tools=["read_value"])
        self.assertEqual(validated["revisions"][0]["mode"], "CHANGE")

    def test_calendar_diagnostic_still_selects_informative_real_action(self):
        original = calendar_bundle()
        source = copy.deepcopy(original)
        for item in source["programs"] + source["diagnostics"]:
            item["program"] = example_as_observe(item["program"])
        reference_world, new_world = CalendarWorld(), CalendarWorld()
        reference = Runtime(reference_world.registry()).run("calendar", bundle=original)
        result = Runtime(new_world.registry()).run("calendar", bundle=lower_bundle(source))
        self.assertEqual((result.status, result.value), (reference.status, reference.value))
        self.assertEqual(new_world.events["A"], 930)
        self.assertEqual(new_world.calls, reference_world.calls)
        selected = [r for r in result.reports if r["kind"] == "action_selected"]
        self.assertEqual(selected[0]["candidate"], "verify_time")
        self.assertEqual(result.budget["tool_calls"], 2)

    def test_pagination_shared_effects_and_pure_loop_are_preserved(self):
        source = pagination_bundle()
        for item in source["programs"]:
            item["program"] = example_as_observe(item["program"])
        world = PaginationWorld()
        result = Runtime(world.registry()).run("pages", bundle=lower_bundle(source))
        self.assertEqual((result.status, result.value), ("completed", ["a", "b", "c"]))
        self.assertEqual(result.budget["tool_calls"], 2)
        selected = [r for r in result.reports if r["kind"] == "action_selected"]
        self.assertEqual(set(selected[0]["shared_with"]), {"items_parser", "data_parser"})

    def test_replan_only_diagnostic_does_not_gain_discrimination(self):
        source = calendar_bundle()
        diag = source["diagnostics"][0]
        diag["program"] = observe("read_event")
        diag["program"]["blocks"]["main"]["term"]["args"] = {"id": "B"}
        diag["program"]["blocks"]["ok"]["term"] = {
            "op": "replan",
            "reason": "Think",
            "state": {"var": "result"},
        }
        report = evaluate_diagnostic(lower_bundle(source)["diagnostics"][0])
        self.assertEqual(report.score, 0)
        self.assertFalse(report.eligible)
        self.assertTrue(
            all(w["hypothetical"] and not w["contract_evidence"] for w in report.witness_results)
        )

    def test_documented_workspace_source(self):
        path = Path(__file__).resolve().parents[1] / "examples/observe/workspace.source.json"
        program = json.loads(path.read_text())
        result = self.boundary(
            program,
            {
                "status": "returned",
                "value": {
                    "workspace_root": "/fixture/workspace",
                    "relative_path_base": "/fixture/workspace",
                    "process_cwd": "/different/process",
                },
            },
        )
        self.assertEqual(result.value, "当前工作区：/fixture/workspace")
