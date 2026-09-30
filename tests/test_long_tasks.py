# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import unittest
from pathlib import Path

from flora.engine.budget import Budget, BudgetLimits
from flora.engine.runtime import Runtime, RuntimeConfig
from flora.general.agent import GeneralAgent
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import ScriptedCompiler
from flora.state.trace import SQLiteTrace
from flora.support.errors import InterruptedEffect, ValidationError
from tests.helpers import block, bundle, pure


def sequence(tool="tick", count=70):
    blocks = {}
    for index in range(count):
        params = [] if index == 0 else ["reply"]
        blocks[f"b{index}"] = block(
            params,
            term={
                "op": "effect",
                "tool": tool,
                "args": {},
                "resume": f"b{index + 1}",
                "bind": "reply",
                "capture": {},
            },
        )
    blocks[f"b{count}"] = block(["reply"], term={"op": "return", "value": "done"})
    return {"version": 1, "entry": "b0", "blocks": blocks}


class LongTaskTests(unittest.TestCase):
    def test_committed_slice_restores_same_program_and_never_repeats_effects(self):
        calls = []

        def tick() -> int:
            calls.append(len(calls))
            return len(calls)

        tools = make_registry([tick])
        with tempfile.TemporaryDirectory() as root:
            trace = SQLiteTrace(Path(root) / "trace.sqlite")
            runtime = Runtime(
                tools, trace=trace, config=RuntimeConfig(max_steps=None, max_compile_cycles=None)
            )
            first = runtime.run("Long task", bundle=bundle(sequence()), slice_steps=32)
            self.assertEqual(first.status, "yielded")
            self.assertEqual(len(calls), 32)
            runtime = Runtime.restore(tools, trace)
            second = runtime.run(slice_steps=32)
            self.assertEqual(second.status, "yielded")
            self.assertEqual(len(calls), 64)
            runtime = Runtime.restore(tools, trace)
            final = runtime.run(slice_steps=32)
            self.assertEqual(final.status, "completed")
            self.assertEqual(calls, list(range(70)))
            self.assertEqual(final.budget["tool_calls"], 70)
            self.assertEqual(final.epoch, 70)
            trace.close()

    def test_explicit_limits_remain_cumulative_across_slices(self):
        def tick() -> int:
            return 1

        runtime = Runtime(
            make_registry([tick]),
            config=RuntimeConfig(max_steps=None),
            budget=Budget(BudgetLimits(max_tool_calls=3)),
        )
        self.assertEqual(
            runtime.run("task", bundle=bundle(sequence()), slice_steps=2).status, "yielded"
        )
        result = runtime.run(slice_steps=2)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.budget["tool_calls"], 3)
        self.assertEqual(len(runtime.trace.records), 3)

    def test_unknown_action_never_yields_into_replay(self):
        calls = []

        def tick() -> int:
            calls.append(1)
            raise InterruptedEffect("Actual outcome unknown")

        runtime = Runtime(make_registry([tick]), config=RuntimeConfig(max_steps=None))
        result = runtime.run("task", bundle=bundle(sequence()), slice_steps=1)
        self.assertEqual(result.status, "interrupted_unknown")
        restored = Runtime.restore(runtime.tools, runtime.trace)
        self.assertEqual(restored.run(slice_steps=1).status, "interrupted_unknown")
        self.assertEqual(calls, [1])

    def test_worker_wait_does_not_trigger_model_polling(self):
        guard = {"ready": False, "waiting": True, "worker": "a-real"}
        compiler = ScriptedCompiler([bundle(pure("answer"))])
        runtime = Runtime(make_registry([]), compiler=compiler, completion_guard=lambda: guard)
        result = runtime.run("task")
        self.assertEqual(result.status, "waiting")
        self.assertEqual(runtime.compile_cycles, 1)
        self.assertEqual(runtime.run().status, "waiting")
        self.assertEqual(runtime.compile_cycles, 1)
        guard.update(ready=True, waiting=False)
        self.assertEqual(runtime.run().status, "completed")
        restored = Runtime.restore(runtime.tools, runtime.trace, completion_guard=lambda: guard)
        self.assertEqual(restored.run().status, "completed")

    def test_structured_rejection_reports_actual_unfinished_work(self):
        runtime = Runtime(
            make_registry([]),
            config=RuntimeConfig(max_steps=1),
            completion_guard=lambda: {"ready": False, "unreviewed_workers": ["a-real"]},
        )
        result = runtime.run("task", bundle=bundle(pure("premature")))
        self.assertEqual(result.status, "incomplete")
        rejection = next(r for r in result.reports if r["kind"] == "completion_rejected")
        self.assertEqual(rejection["details"]["unreviewed_workers"], ["a-real"])
        self.assertFalse(rejection["correctness_feedback"])

    def test_general_automatic_slices_do_not_compile_again(self):
        class Provider:
            calls = 0

            def complete(self, messages, *, max_tokens):
                self.calls += 1
                context = json.loads(messages[1]["content"])
                return ModelResponse(
                    json.dumps(
                        bundle(
                            sequence("workspace_context", 70),
                            context["epoch"],
                            context["trace_digest"],
                        )
                    ),
                    10,
                    10,
                )

        provider = Provider()
        with tempfile.TemporaryDirectory() as root:
            with GeneralAgent(
                session_dir=Path(root) / "session", workspace=root, provider=provider
            ) as app:
                result = app.run("Read workspace 70 times as a deliberate scheduling test")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["budget"]["tool_calls"], 70)
                self.assertEqual(provider.calls, 1)
                self.assertFalse(app.status()["requires_resume"])
                self.assertTrue(result["completion_checks"]["ready"])

    def test_invalid_slice_rejected_without_model_or_tool_call(self):
        for invalid in (True, 0, -1, "32"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Runtime(make_registry([])).run("task", bundle=bundle(), slice_steps=invalid)


class WorkLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = GeneralAgent(
            session_dir=self.root / "session", workspace=self.root, provider=object_provider()
        )
        self.app.task = {"key": "current", "task": "Research"}
        self.app.work.begin("current", "Research")

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def step(self, **fields):
        return {
            "id": "research",
            "goal": "Inspect actual evidence",
            "status": "pending",
            "required": True,
            "evidence": [],
            "note": "",
            **fields,
        }

    def test_required_goals_and_revision_are_retained(self):
        self.app.work.update_work([self.step()], 0)
        self.assertFalse(self.app._ready_to_finish()["ready"])
        with self.assertRaisesRegex(ValidationError, "revision"):
            self.app.work.update_work([self.step()], 0)
        with self.assertRaisesRegex(ValidationError, "retained"):
            self.app.work.update_work([self.step(required=False)], 1)
        self.app.work.update_work(
            [self.step(status="blocked", note="The configured source is unavailable")], 1
        )
        self.assertTrue(self.app._ready_to_finish()["ready"])
        self.assertEqual(
            self.app.work.completion()["limitations"][0]["note"],
            "The configured source is unavailable",
        )

    def test_file_evidence_invalidated_when_file_changes(self):
        import hashlib

        file = self.root / "data.txt"
        file.write_text("real")
        sha = hashlib.sha256(b"real").hexdigest()
        self.app.work.update_work(
            [self.step(status="completed", evidence=[{"path": "data.txt", "sha256": sha}])], 0
        )
        self.assertTrue(self.app._ready_to_finish()["ready"])
        file.write_text("changed")
        self.assertFalse(self.app._ready_to_finish()["ready"])
        self.assertEqual(self.app.work.completion()["stale_evidence"], ["research"])

    def test_source_evidence_checked_and_declarations_not_truth(self):
        source = self.app.store.record(origin="fixture", title="Title", text="actual")
        result = self.app.work.update_work(
            [self.step(status="completed", evidence=[{"source_id": source["source_id"]}])], 0
        )
        self.assertFalse(result["claims_verified"])
        self.assertEqual(result["steps"][0]["evidence"][0]["sha256"], source["sha256"])
        self.app.work.update_work(
            [self.step(status="completed", evidence=[{"source_id": source["source_id"]}])], 1
        )

    def test_invalid_status_is_validation_failure_without_state_change(self):
        for status in ([], {}, True):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                self.app.work.update_work([self.step(status=status)], 0)
        self.assertEqual(self.app.work.read_work()["revision"], 0)
        self.assertFalse(self.app.work.read_work()["steps"])


def object_provider():
    class Provider:
        def complete(self, messages, *, max_tokens):
            context = json.loads(messages[1]["content"])
            return ModelResponse(
                json.dumps(bundle(pure("hello"), context["epoch"], context["trace_digest"])), 1, 1
            )

    return Provider()
