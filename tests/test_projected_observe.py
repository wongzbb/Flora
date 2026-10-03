# SPDX-License-Identifier: Apache-2.0
"""Opt-in source projection changes declarations, never effect or recovery policy."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from flora.engine.runtime import Runtime
from flora.examples import CalendarWorld, calendar_bundle
from flora.general.agent import GeneralAgent
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler, validate_bundle
from flora.language.frontend import lower_bundle
from flora.language.prompts import compact_prompt, focused_prompt
from flora.language.vm import new_machine, resume, run_until_boundary
from flora.state.opaque import OPAQUE_KEY
from flora.support.errors import CompilerError, StaleAnchor, ValidationError
from tests.helpers import block, bundle, context, pure
from tests.test_compact import listed
from tests.test_frontend import example_as_observe, observe
from tests.test_recovery import SequenceProvider
from tests.test_revision_opportunities import (
    batch_consumer,
    migrate_observed_reply,
    revised_consumer,
)

TOOLS = [{"name": "read_value", "parameters": {"type": "object", "properties": {}}}]


def projected():
    program = observe(capture={"left": 11, "right": 22, "unused": 33})
    program["blocks"]["ok"] = block(
        ["left", "result"],
        term={"op": "return", "value": {"left": {"var": "left"}, "value": {"var": "result"}}},
    )
    program["blocks"]["err"] = block(
        ["right", "result"],
        term={"op": "return", "value": {"right": {"var": "right"}, "error": {"var": "result"}}},
    )
    return program


def lowered(program):
    return lower_bundle(bundle(listed(program)), syntax="block-list-v3")["programs"][0]["program"]


class ProjectedObserveTests(unittest.TestCase):
    def test_independent_subsets_keep_full_effect_capture_and_project_final_jump(self):
        source = projected()
        before = copy.deepcopy(source)
        program = lowered(source)
        waiting = run_until_boundary(new_machine(program))
        self.assertEqual(waiting.kind, "effect")
        self.assertEqual(waiting.request, {"tool": "read_value", "args": {}})
        self.assertEqual(
            waiting.machine.pending["frame"]["capture"], {"left": 11, "right": 22, "unused": 33}
        )
        self.assertEqual(source, before)
        result = run_until_boundary(resume(waiting.machine, {"status": "returned", "value": 5}))
        self.assertEqual(result.value, {"left": 11, "value": 5})
        error = {"type": "ValueError", "message": "observed"}
        result = run_until_boundary(resume(waiting.machine, {"status": "raised", "error": error}))
        self.assertEqual(result.value, {"right": 22, "error": error})
        unknown = run_until_boundary(
            resume(waiting.machine, {"status": "interrupted_unknown", "error": error})
        )
        self.assertEqual(unknown.kind, "unknown")
        self.assertEqual(lowered(program), program)

    def test_both_targets_may_receive_bind_only_and_preserve_opaque_value(self):
        program = observe(capture={"unused": 3})
        for name in ("ok", "err"):
            program["blocks"][name]["params"] = ["result"]
        carrier = {OPAQUE_KEY: {"token": "offline-carrier", "type": "Example"}}
        waiting = run_until_boundary(new_machine(lowered(program)))
        actual = run_until_boundary(
            resume(waiting.machine, {"status": "returned", "value": carrier})
        )
        self.assertEqual(actual.value, carrier)

    def test_target_may_alpha_rename_the_raw_result_parameter(self):
        program = observe(capture={})
        program["blocks"]["ok"]["params"] = ["value_local"]
        program["blocks"]["err"]["params"] = ["error_local"]
        program["blocks"]["ok"]["term"] = {
            "op": "return",
            "value": {"var": "value_local"},
        }
        program["blocks"]["err"]["term"] = {
            "op": "return",
            "value": {"var": "error_local"},
        }
        waiting = run_until_boundary(new_machine(lowered(program)))
        returned = run_until_boundary(resume(waiting.machine, {"status": "returned", "value": 7}))
        self.assertEqual(returned.value, 7)
        waiting = run_until_boundary(new_machine(lowered(program)))
        error = {"type": "ValueError", "message": "observed"}
        raised = run_until_boundary(resume(waiting.machine, {"status": "raised", "error": error}))
        self.assertEqual(raised.value, error)

    def test_shared_error_handler_accepts_different_declared_capture_sets(self):
        program = observe(capture={"first": 1})
        program["blocks"]["err"]["params"] = ["result"]
        program["blocks"]["ok"]["term"] = {
            "op": "observe",
            "tool": "read_value",
            "args": {},
            "capture": {"second": 2},
            "bind": "result",
            "success": "done",
            "error": "err",
        }
        program["blocks"]["done"] = block(
            ["result"], term={"op": "return", "value": {"var": "result"}}
        )
        first = run_until_boundary(new_machine(lowered(program)))
        second = run_until_boundary(resume(first.machine, {"status": "returned", "value": 1}))
        error = {"type": "ValueError", "message": "second observation"}
        final = run_until_boundary(resume(second.machine, {"status": "raised", "error": error}))
        self.assertEqual(final.value, {"error": error})

    def test_missing_bind_extra_or_duplicate_parameters_are_rejected(self):
        for params in ([], ["left"], ["result", "invented"], ["result", "result"], [7, "result"]):
            with self.subTest(params=params):
                program = projected()
                program["blocks"]["err"]["params"] = params
                with self.assertRaises(ValidationError):
                    lowered(program)
        for field, value in (("bind", "left"), ("error", "missing"), ("error", [])):
            with self.subTest(field=field):
                program = projected()
                program["blocks"]["main"]["term"][field] = value
                with self.assertRaises(ValidationError):
                    lowered(program)

    def test_projection_never_defines_undeclared_or_missing_variables(self):
        program = projected()
        program["blocks"]["err"]["term"]["value"] = {"var": "left"}
        with self.assertRaisesRegex(ValidationError, "undefined variable"):
            lowered(program)
        program = projected()
        program["blocks"]["main"]["term"]["capture"]["unused"] = {"var": "absent"}
        with self.assertRaisesRegex(ValidationError, "undefined variable"):
            lowered(program)

    def test_unused_capture_expression_still_faults_before_any_effect(self):
        calls = []

        def read_value():
            calls.append(1)
            return 7

        program = projected()
        program["blocks"]["main"]["term"]["capture"]["unused"] = {
            "op": "get",
            "args": [{}, "absent"],
        }
        result = Runtime(make_registry([read_value])).run(
            "observe", bundle=bundle(lowered(program))
        )
        self.assertEqual(result.status, "needs_program")
        self.assertEqual(calls, [])
        self.assertTrue(
            any(r["kind"] == "local_execution" and r["status"] == "fault" for r in result.reports)
        )

    def test_projection_keeps_core_parameter_and_expansion_limits(self):
        program = observe(capture={f"v{i}": i for i in range(128)})
        for name in ("ok", "err"):
            program["blocks"][name]["params"] = ["result"]
        with self.assertRaisesRegex(ValidationError, "128"):
            lowered(program)
        program = projected()
        program["blocks"].update(
            {f"unused_{i}": block(term={"op": "return", "value": 0}) for i in range(508)}
        )
        with self.assertRaisesRegex(ValidationError, "512"):
            lowered(program)

    def test_full_parameter_sources_and_plain_ir_lower_identically(self):
        for program in (observe(capture={"kept": 3}), pure({"literal": {"op": "observe"}})):
            source = bundle(listed(program))
            self.assertEqual(
                lower_bundle(source, syntax="block-list-v3"),
                lower_bundle(source, syntax="block-list-v2"),
            )
        for syntax in ("observe-v1", "block-list-v1", "block-list-v2"):
            with self.subTest(syntax=syntax), self.assertRaises(ValidationError):
                lower_bundle(bundle(projected()), syntax=syntax)

    def test_fresh_names_and_saved_ir_are_not_reinterpreted(self):
        program = projected()
        program["blocks"]["main"]["term"]["capture"]["__flora_observe_0_reply"] = 9
        program["blocks"]["__flora_observe_1_ok"] = block(term={"op": "return", "value": 0})
        ir = lowered(program)
        self.assertIn("__flora_observe_2_check", ir["blocks"])
        self.assertEqual(lowered(program), ir)
        self.assertEqual(
            lower_bundle(bundle(ir), syntax="block-list-v3"),
            lower_bundle(bundle(ir), syntax="block-list-v2"),
        )

    def test_core_effect_and_branch_rules_are_not_relaxed(self):
        program = observe(capture={"unused": 1})
        program["blocks"]["main"]["term"] = {
            "op": "effect",
            "tool": "read_value",
            "args": {},
            "bind": "result",
            "capture": {"unused": 1},
            "resume": "ok",
        }
        program["blocks"]["ok"]["params"] = ["result"]
        with self.assertRaisesRegex(ValidationError, "resume parameters"):
            lowered(program)
        program["blocks"]["main"]["term"] = {
            "op": "branch",
            "condition": True,
            "yes": "ok",
            "no": "err",
            "args": {"result": 1},
        }
        with self.assertRaisesRegex(ValidationError, r"argument keys.*expected.*got"):
            lowered(program)

    def test_actual_diagnostic_insertion_and_contract_capture_still_work(self):
        source = calendar_bundle()
        for item in source["programs"] + source["diagnostics"]:
            program = example_as_observe(item["program"])
            for b in program["blocks"].values():
                if b["term"]["op"] == "observe":
                    b["term"]["capture"]["unused_projection"] = 7
            item["program"] = listed(program)
        world = CalendarWorld()
        result = Runtime(world.registry()).run(
            "Move the event", bundle=lower_bundle(source, syntax="block-list-v3")
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual([c["tool"] for c in world.calls], ["read_event", "move_event"])
        self.assertEqual(world.events["A"], 930)
        self.assertTrue(
            any(r["kind"] == "action_selected" and r["diagnostic"] for r in result.reports)
        )
        self.assertTrue(any(r["kind"] == "consumer_check" for r in result.reports))

    def test_revision_bundles_retain_actual_history_and_current_gates(self):
        calls = []

        def read_batch():
            calls.append(1)
            return {"items": ["a", "b"]}

        runtime = Runtime(make_registry([read_batch]))
        self.assertEqual(
            runtime.run("Count items", bundle=bundle(batch_consumer())).status, "needs_program"
        )
        program, migration = revised_consumer(), migrate_observed_reply()
        source = bundle(listed(program), runtime.trace.epoch, runtime.trace.digest)
        source["programs"][0]["inputs"] = {"record": {}}
        source["revisions"] = [
            {
                "id": "extend",
                "target_candidate": "main",
                "program": listed(program),
                "migration": listed(migration),
                "mode": "EXTEND",
            }
        ]
        result = runtime.run("Count items", bundle=lower_bundle(source, syntax="block-list-v3"))
        self.assertEqual((result.status, result.value, calls), ("completed", 2, [1]))
        revision = next(r for r in result.reports if r["kind"] == "revision_checked")
        self.assertTrue(revision["accepted"])
        self.assertTrue(all(r["verdict"] == "PASS" for r in revision["results"]))
        self.assertEqual(revision["current_check"]["verdict"], "PASS")
        invalid = projected()
        invalid["blocks"]["main"]["params"] = ["context"]
        source["revisions"][0]["migration"] = listed(invalid)
        with self.assertRaises(ValidationError):
            validate_bundle(lower_bundle(source, syntax="block-list-v3"))

    def test_compiler_supports_v3_without_weakening_anchors_or_tool_schemas(self):
        source = bundle(listed(projected()))
        compiler = LLMCompiler(
            SequenceProvider(ModelResponse(json.dumps(source), 1, 1)),
            syntax="block-list-v3",
            prompt_style="compact-v2",
            max_repairs=0,
        )
        self.assertEqual(
            compiler.compile(context(TOOLS)), lower_bundle(source, syntax="block-list-v3")
        )
        for edit, error in (
            ("anchor", StaleAnchor),
            ("tool", CompilerError),
            ("schema", CompilerError),
        ):
            with self.subTest(edit=edit):
                bad = copy.deepcopy(source)
                if edit == "anchor":
                    bad["expected_epoch"] = 1
                elif edit == "tool":
                    bad["programs"][0]["program"][0]["term"]["tool"] = "ungranted"
                else:
                    bad["programs"][0]["program"][0]["term"]["args"] = 7
                compiler = LLMCompiler(
                    SequenceProvider(ModelResponse(json.dumps(bad), 1, 1)),
                    syntax="block-list-v3",
                    prompt_style="compact-v2",
                    max_repairs=0,
                )
                with self.assertRaises(error):
                    compiler.compile(context(TOOLS))

    def test_version_specific_prompts_keep_exact_core_and_legacy_rules(self):
        for make in (compact_prompt, focused_prompt):
            v2, v3 = make("block-list-v2"), make("block-list-v3")
            self.assertNotIn("SOURCE block-list-v3", v2)
            self.assertIn("SOURCE block-list-v3", v3)
            self.assertIn("ALL capture expressions still evaluate before the effect", v3)
            self.assertIn("subset of capture keys", v3)
            self.assertIn("Always write both target blocks explicitly", v3)
            self.assertNotIn("their params are exactly capture keys plus bind", v3)
            self.assertNotIn("with params exactly\ncapture+bind", v3)
            self.assertIn("Resume params", v3)
        prompt = focused_prompt("block-list-v3")
        for line in prompt.splitlines():
            if line.startswith('{"programs":'):
                validate_bundle(lower_bundle(json.loads(line), syntax="block-list-v3"))

    def test_profile_opt_in_and_saved_v2_fingerprint_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            for name, options, expected in (
                ("default", {}, "block-list-v2"),
                ("legacy", {"syntax": "block-list-v2"}, "block-list-v2"),
                ("new", {"syntax": "block-list-v3"}, "block-list-v3"),
            ):
                with self.subTest(name=name):
                    path = root / name
                    with GeneralAgent(
                        session_dir=path,
                        workspace=workspace,
                        provider=SequenceProvider(),
                        profile={"compiler": options},
                    ) as app:
                        identity = app.agent._fingerprint
                        descriptions = app.agent.tools.descriptions()
                        self.assertEqual(app.agent.compiler.syntax, expected)
                    with GeneralAgent(session_dir=path, provider=SequenceProvider()) as app:
                        self.assertEqual(app.agent._fingerprint, identity)
                        self.assertEqual(app.agent.tools.descriptions(), descriptions)
                        self.assertEqual(app.agent.compiler.syntax, expected)


if __name__ == "__main__":
    unittest.main()
