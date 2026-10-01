# SPDX-License-Identifier: Apache-2.0
"""Explicit task obligations gate termination without a host semantic solver."""

import copy
import tempfile
import unittest
from pathlib import Path

from flora.engine.runtime import Runtime
from flora.general.agent import GeneralAgent, _normalize
from flora.general.storage import atomic_json
from flora.language.frontend import lower_bundle
from flora.support.errors import ValidationError
from tests.helpers import bundle, pure
from tests.test_frontend import observe
from tests.test_long_tasks import object_provider


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = GeneralAgent(
            session_dir=self.root / "session",
            workspace=self.root,
            provider=object_provider(),
            profile={"general": {"require_task_completion": True}},
        )
        self.app.task = {
            "key": "current",
            "task": "Read the route, then verify the selected source",
        }
        atomic_json(self.root / "session/task.json", self.app.task)
        self.app.work.begin("current", self.app.task["task"])

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def test_partial_real_read_cannot_finish_an_undeclared_task(self):
        (self.root / "route.json").write_text('{"file":"selected.json"}')
        program = observe("read_file")
        program["blocks"]["main"]["term"]["args"] = {"path": "route.json"}
        runtime = Runtime(self.app.agent.tools, completion_guard=self.app._ready_to_finish)
        result = runtime.run(self.app.task["task"], bundle=lower_bundle(bundle(program)))
        self.assertEqual(result.status, "needs_program")
        self.assertEqual(len(runtime.trace.records), 1)
        self.assertEqual(runtime.trace.records[0]["status"], "returned")
        rejected = next(r for r in result.reports if r["kind"] == "completion_rejected")
        self.assertEqual(rejected["details"]["work"]["pending_steps"], ["task"])
        self.assertFalse(rejected["correctness_feedback"])

    def test_required_obligations_cannot_be_removed_downgraded_or_rewritten(self):
        original = self.app.work.read_work()["steps"]
        for kind in ("remove", "optional", "rewrite"):
            steps = copy.deepcopy(original)
            if kind == "remove":
                steps[0]["id"] = "another"
            elif kind == "optional":
                steps[0]["required"] = False
            else:
                steps[0]["goal"] = "Only read the route"
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                self.app.work.update_work(steps, 0)
        self.assertEqual(self.app.work.read_work()["steps"], original)
        extra = {**original[0], "id": "verify", "goal": "Verify selected source"}
        self.app.work.update_work([*original, extra], 0)
        extra["goal"] = "Read only route"
        with self.assertRaisesRegex(ValidationError, "immutable"):
            self.app.work.update_work([*original, extra], 1)

    def test_root_claim_cannot_bypass_pending_substeps(self):
        root = self.app.work.read_work()["steps"][0]
        extra = {**root, "id": "verify", "goal": "Verify selected source"}
        root["status"] = "completed"
        self.app.work.update_work([root, extra], 0)
        result = self.app.work.completion()
        self.assertFalse(result["ready"])
        self.assertEqual(result["pending_steps"], ["verify"])
        extra["status"] = "completed"
        self.app.work.update_work([root, extra], 1)
        self.assertTrue(self.app.work.completion()["ready"])
        self.assertFalse(self.app.work.completion()["claims_verified"])

    def test_explicit_blocked_outcome_retains_limitation_not_success_evidence(self):
        root = self.app.work.read_work()["steps"][0]
        root["status"] = "blocked"
        with self.assertRaises(ValidationError):
            self.app.work.update_work([root], 0)
        root["note"] = "The selected source cannot be accessed"
        self.app.work.update_work([root], 0)
        result = self.app.work.completion()
        self.assertTrue(result["ready"])
        self.assertEqual(result["limitations"], [{"id": "task", "note": root["note"]}])
        self.assertFalse(result["claims_verified"])

    def test_contract_and_exact_task_survive_reopen(self):
        identity = self.app.agent._fingerprint
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=object_provider())
        self.assertEqual(self.app.agent._fingerprint, identity)
        self.assertEqual(self.app.work.read_work()["task"], self.app.task["task"])
        self.assertEqual(self.app.work.completion()["pending_steps"], ["task"])
        self.app.task = {"key": "next", "task": "A new request"}
        self.app.work.begin("next", "A new request")
        self.assertEqual(self.app.work.read_work()["task_key"], "next")
        self.assertEqual(self.app.work.read_work()["revision"], 0)

    def test_legacy_default_remains_unchanged(self):
        with GeneralAgent(
            session_dir=self.root / "legacy", workspace=self.root, provider=object_provider()
        ) as legacy:
            legacy.task = {"key": "old", "task": "hello"}
            legacy.work.begin("old", "hello")
            self.assertEqual(legacy.work.read_work()["steps"], [])
            result = Runtime(legacy.agent.tools, completion_guard=legacy._ready_to_finish).run(
                "hello", bundle=bundle(pure("hello"))
            )
            self.assertEqual((result.status, result.value), ("completed", "hello"))

    def test_interruption_between_task_and_work_writes_cannot_reuse_old_completion(self):
        root = self.app.work.read_work()["steps"][0]
        root["status"] = "completed"
        self.app.work.update_work([root], 0)
        old_work = self.app.work.read_work()
        new_task = {"key": "new", "task": "A different unfinished request"}
        atomic_json(self.root / "session/task.json", new_task)
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=object_provider())
        completion = self.app._ready_to_finish()
        self.assertFalse(completion["ready"])
        self.assertFalse(completion["work"]["task_binding_valid"])
        self.assertIn("different task", completion["work"]["recovery_required"])
        self.assertEqual(self.app.work.read_work(), old_work)
        with self.assertRaisesRegex(ValidationError, "another task"):
            self.app.work.update_work([root], 1)
        self.assertFalse(self.app.agent.status()["requires_resume"])
        self.app.work.begin(new_task["key"], new_task["task"])
        self.assertTrue(self.app.work.completion()["task_binding_valid"])
        self.assertEqual(self.app.work.completion()["pending_steps"], ["task"])

    def test_changed_task_text_with_same_key_does_not_inherit_completion(self):
        root = self.app.work.read_work()["steps"][0]
        root["status"] = "completed"
        self.app.work.update_work([root], 0)
        self.app.task["task"] = "A different request under a reused key"
        self.assertFalse(self.app.work.completion()["task_binding_valid"])
        self.assertFalse(self.app._ready_to_finish()["ready"])

    def test_flag_is_typed_and_requires_durable_work_protocol(self):
        for general in (
            {"require_task_completion": "yes"},
            {"require_task_completion": 1},
            {"require_task_completion": True, "protocol": "general-v3"},
        ):
            with self.subTest(general=general), self.assertRaises(ValidationError):
                _normalize({"general": general})


if __name__ == "__main__":
    unittest.main()
