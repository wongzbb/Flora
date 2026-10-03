# SPDX-License-Identifier: Apache-2.0
"""Deterministic recovery, scheduling and durable collection regressions."""

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import Future
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import GeneralAgent
from flora.general.coordinator import Coordinator
from flora.general.storage import atomic_json
from flora.integrations.providers import ModelResponse
from flora.support.errors import ValidationError
from tests.helpers import bundle, pure


class RecoveryProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        context = json.loads(messages[1]["content"])
        return ModelResponse(
            json.dumps(
                bundle(
                    pure({"handoff": context["memory"]["data"]}),
                    context["epoch"],
                    context["trace_digest"],
                )
            ),
            11,
            7,
        )


class CoordinatorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.provider = RecoveryProvider()
        self.app = GeneralAgent(
            session_dir=self.root / "session",
            workspace=self.workspace,
            provider=self.provider,
            profile={
                "general": {"subagents": {"enabled": True, "max_parallel": 1, "max_children": 8}}
            },
        )
        self._set_task()
        self.gates = []

    def test_contract_type_uses_top_level_array_before_nested_words(self):
        self.assertEqual(Coordinator._contract_type("array of objects with path and type"), "array")
        self.assertEqual(Coordinator._contract_type("object with fields"), "object")
        self.assertEqual(Coordinator._contract_type("最终答案字符串"), "string")
        self.assertEqual(Coordinator._contract_type("输出为数值"), "number")

    def test_structured_contract_checks_nested_items_and_required_fields(self):
        contract = {
            "outputs": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["path", "type"],
                        "properties": {
                            "path": "string",
                            "type": "string",
                        },
                    },
                }
            }
        }
        good = Coordinator._contract_observation(
            contract, {"entries": [{"path": "a.txt", "type": "file"}]}
        )
        bad = Coordinator._contract_observation(contract, {"entries": [{"path": 3}]})
        self.assertEqual(good["status"], "pass")
        self.assertEqual(bad["status"], "violation")
        self.assertTrue(any("missing output entries[0].type" in item for item in bad["violations"]))

    def test_contracted_child_cannot_be_marked_optional(self):
        with self.assertRaisesRegex(ValidationError, "must be required"):
            self.coordinator.spawn_agent(
                "inspect the assigned source",
                required=False,
                context={"contract": {"outputs": {"value": "number"}}},
            )

    def test_unknown_contract_output_cannot_be_accepted(self):
        observed = self.coordinator._contract_observation(
            {"outputs": {"value": "future custom type"}}, {"value": 3}
        )
        self.assertEqual(observed["status"], "unknown")
        self.assertEqual(observed["unknown"], ["value"])

    def _set_task(self):
        self.app.task = {"key": "current", "task": "Recover coordinated research"}
        self.app.work.begin("current", self.app.task["task"])

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.app.close()
        self.tmp.cleanup()

    @property
    def coordinator(self):
        return self.app.delegation

    def spawn(self, task, **kwargs):
        ident = self.coordinator.spawn_agent(task, **kwargs)["agent_id"]
        self.coordinator.futures[ident].result(timeout=5)
        return ident

    def occupy_pool(self):
        entered, release = threading.Event(), threading.Event()
        self.gates.append(release)

        def hold():
            entered.set()
            release.wait(5)

        future = self.coordinator.pool.submit(hold)
        self.assertTrue(entered.wait(5))
        return release, future

    def review(self, ident, disposition="accepted"):
        page = self.coordinator.read_agent(ident, limit=24000)
        self.assertIsNone(page.get("next_offset"))
        return self.coordinator.review_agent(
            ident, page.get("result_digest", ""), disposition, "Collected the current observation"
        )

    def blocked_child(self):
        first = self.spawn("Prerequisite")
        self.coordinator._update(first, status="needs_program")
        return self.spawn("Dependent", depends_on=[first])

    def test_reverse_resume_cannot_starve_its_prerequisite(self):
        first = self.spawn("Prerequisite")
        second = self.spawn("Dependent", depends_on=[first])
        calls = self.provider.calls
        self.coordinator._update(first, status="interrupted")
        self.coordinator._update(second, status="interrupted")
        release, occupied = self.occupy_pool()
        # Both resumes become visible before either can enter the only slot.
        self.coordinator.resume_agent(second)
        self.coordinator.resume_agent(first)
        release.set()
        occupied.result(timeout=5)
        self.coordinator.futures[first].result(timeout=2)
        self.coordinator.futures[second].result(timeout=2)
        # A prerequisite that was not yet resumed may require explicit recovery
        # of its blocked dependent, but it must never be trapped behind it.
        if self.coordinator.records[second]["status"] == "blocked":
            self.coordinator.resume_agent(second)
            self.coordinator.futures[second].result(timeout=5)
        self.assertEqual(self.coordinator.records[first]["status"], "completed")
        self.assertEqual(self.coordinator.records[second]["status"], "completed")
        self.assertEqual(self.provider.calls, calls, "Completed kernels must not be replayed")

    def test_waiting_dependencies_do_not_enter_the_bounded_pool(self):
        release, occupied = self.occupy_pool()
        first = self.coordinator.spawn_agent("Prerequisite")["agent_id"]
        second = self.coordinator.spawn_agent("Dependent", depends_on=[first])["agent_id"]
        third = self.coordinator.spawn_agent("Final dependent", depends_on=[second])["agent_id"]
        self.assertFalse(self.coordinator.futures[second].running())
        self.assertFalse(self.coordinator.futures[third].running())
        release.set()
        occupied.result(timeout=5)
        result = self.coordinator.wait_agents([first, second, third], timeout=5)
        self.assertEqual([row["status"] for row in result["agents"]], ["completed"] * 3)
        dependencies = self.coordinator._result_view(third)["value"]["handoff"]["dependencies"]
        self.assertEqual(dependencies[0]["agent_id"], second)
        self.assertEqual(dependencies[0]["result"]["status"], "completed")
        self.assertEqual(self.provider.calls, 3)

    def test_pause_settles_undispatched_dependency_futures(self):
        release, occupied = self.occupy_pool()
        first = self.coordinator.spawn_agent("Prerequisite")["agent_id"]
        second = self.coordinator.spawn_agent("Dependent", depends_on=[first])["agent_id"]
        self.coordinator.request_pause()
        self.coordinator.futures[second].result(timeout=1)
        self.assertEqual(self.coordinator.records[second]["status"], "paused")
        release.set()
        occupied.result(timeout=5)
        self.coordinator.futures[first].result(timeout=5)
        self.assertEqual(self.provider.calls, 0)

    def test_invalid_active_resume_preserves_durable_collection(self):
        ident = self.spawn("Research")
        self.review(ident)
        self.coordinator._update(ident, status="paused", no_progress_compiles=7)
        previous = deepcopy(self.coordinator.records[ident])
        self.coordinator.futures[ident] = Future()
        with self.assertRaisesRegex(ValidationError, "already running"):
            self.coordinator.resume_agent(ident)
        self.assertEqual(self.coordinator.records[ident], previous)
        self.coordinator.futures[ident].set_result(None)

    def test_invalid_paused_resume_preserves_durable_collection(self):
        ident = self.spawn("Research")
        self.coordinator._update(ident, status="paused")
        self.review(ident, "blocked")
        previous = deepcopy(self.coordinator.records[ident])
        self.coordinator.request_pause()
        with self.assertRaisesRegex(ValidationError, "paused or closed"):
            self.coordinator.resume_agent(ident)
        self.assertEqual(self.coordinator.records[ident], previous)

    def test_absent_result_must_be_observed_before_review(self):
        ident = self.blocked_child()
        with self.assertRaisesRegex(ValidationError, "Collect the complete"):
            self.coordinator.review_agent(ident, "", "blocked", "Did not inspect the failure")
        self.assertFalse(self.coordinator.read_agent(ident)["result_available"])
        self.coordinator.review_agent(ident, "", "blocked", "Observed dependency failure")

    def test_read_failure_version_must_match_review(self):
        ident = self.blocked_child()
        self.coordinator.read_agent(ident)
        self.coordinator._update(ident, status="interrupted", failure={"code": "different_failure"})
        with self.assertRaisesRegex(ValidationError, "Collect the complete"):
            self.coordinator.review_agent(ident, "", "blocked", "Only read the old failure")
        self.review(ident, "blocked")

    def test_every_review_disposition_is_bound_to_the_current_result(self):
        for disposition in ("accepted", "blocked", "rejected"):
            with self.subTest(disposition=disposition):
                ident = self.spawn("Research " + disposition)
                self.review(ident, disposition)
                self.assertNotIn(ident, self.coordinator.completion()["unreviewed_workers"])
                path = self.coordinator.root / ident / "result.json"
                result = json.loads(path.read_text())
                result["value"] = {"different": "new answer"}
                atomic_json(path, result)
                completion = self.coordinator.completion()
                self.assertFalse(completion["ready"])
                self.assertIn(ident, completion["unreviewed_workers"])

    def test_absent_result_review_is_invalid_after_failure_changes(self):
        ident = self.blocked_child()
        self.review(ident, "blocked")
        self.coordinator._update(ident, status="paused", detail="Different execution boundary")
        self.assertIn(ident, self.coordinator.completion()["unreviewed_workers"])

    def test_collection_windows_survive_restart_without_skipping_bytes(self):
        ident = self.spawn("Research")
        first = self.coordinator.read_agent(ident, limit=10)
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=self.provider)
        self._set_task()
        with self.assertRaisesRegex(ValidationError, "Collect the complete"):
            self.coordinator.review_agent(ident, first["result_digest"], "accepted", "Incomplete")
        self.coordinator.read_agent(ident, offset=10, limit=24000)
        self.coordinator.review_agent(ident, first["result_digest"], "accepted", "Complete")
        self.assertTrue(self.coordinator.completion()["ready"])

    def test_changed_handoff_evidence_blocks_dispatch_without_model_call(self):
        path = self.workspace / "facts.txt"
        path.write_bytes(b"observed")
        release, occupied = self.occupy_pool()
        ident = self.coordinator.spawn_agent(
            "Research the original facts",
            context={
                "files": [{"path": "facts.txt", "sha256": hashlib.sha256(b"observed").hexdigest()}]
            },
        )["agent_id"]
        path.write_bytes(b"changed while queued")
        release.set()
        occupied.result(timeout=5)
        self.coordinator.futures[ident].result(timeout=5)
        row = self.coordinator.records[ident]
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["failure"]["code"], "stale_handoff_evidence")
        self.assertEqual(self.provider.calls, 0)

    def test_missing_completed_dependency_is_not_an_invented_handoff(self):
        first = self.spawn("Prerequisite")
        (self.coordinator.root / first / "result.json").unlink()
        calls = self.provider.calls
        second = self.spawn("Dependent", depends_on=[first])
        self.assertEqual(self.coordinator.records[second]["status"], "blocked")
        self.assertEqual(self.provider.calls, calls)

    def test_registry_completed_does_not_accept_an_incomplete_result(self):
        ident = self.spawn("Research")
        path = self.coordinator.root / ident / "result.json"
        result = json.loads(path.read_text())
        result["status"] = "yielded"
        atomic_json(path, result)
        page = self.coordinator.read_agent(ident, limit=24000)
        with self.assertRaisesRegex(ValidationError, "unfinished child"):
            self.coordinator.review_agent(ident, page["result_digest"], "accepted", "Incomplete")

    def test_submission_failure_can_be_explicitly_resumed_without_stuck_future(self):
        with patch.object(self.coordinator.pool, "submit", side_effect=RuntimeError("no thread")):
            ident = self.coordinator.spawn_agent("Research")["agent_id"]
        self.assertEqual(self.coordinator.records[ident]["status"], "interrupted")
        with self.assertRaisesRegex(RuntimeError, "no thread"):
            self.coordinator.futures[ident].result(timeout=1)
        self.coordinator.resume_agent(ident)
        self.coordinator.futures[ident].result(timeout=5)
        self.assertEqual(self.coordinator.records[ident]["status"], "completed")
        self.assertEqual(self.provider.calls, 1)

    def test_unexpected_worker_exit_does_not_leave_a_live_registry_entry(self):
        with patch.object(self.coordinator, "_run", side_effect=RuntimeError("private detail")):
            ident = self.coordinator.spawn_agent("Research")["agent_id"]
            with self.assertRaises(RuntimeError):
                self.coordinator.futures[ident].result(timeout=5)
        self.assertEqual(self.coordinator.records[ident]["status"], "interrupted")
        self.assertFalse(self.coordinator.is_busy())
        self.assertNotIn("private detail", (self.coordinator.root / "children.json").read_text())
        second = self.spawn("Dependent", depends_on=[ident])
        self.assertEqual(self.coordinator.records[second]["status"], "blocked")
        self.assertEqual(self.provider.calls, 0)

    def test_blocked_registry_failure_settles_future_and_continues_dispatch(self):
        release, occupied = self.occupy_pool()
        first = self.coordinator.spawn_agent("Prerequisite")["agent_id"]
        second = self.coordinator.spawn_agent("Dependent", depends_on=[first])["agent_id"]
        third = self.coordinator.spawn_agent("Other dependent", depends_on=[first])["agent_id"]
        original_run, original_save = self.coordinator._run, self.coordinator._save
        failure = OSError("transient registry write")
        failed = False

        def interrupt_prerequisite(ident):
            if ident == first:
                self.coordinator._update(ident, status="interrupted")
            else:
                original_run(ident)

        def save():
            nonlocal failed
            if not failed and self.coordinator.records[second]["status"] == "blocked":
                failed = True
                raise failure
            original_save()

        with patch.object(self.coordinator, "_run", side_effect=interrupt_prerequisite):
            with patch.object(self.coordinator, "_save", side_effect=save):
                release.set()
                occupied.result(timeout=5)
                self.coordinator.futures[first].result(timeout=5)
                with self.assertRaises(OSError) as caught:
                    self.coordinator.futures[second].result(timeout=1)
                self.assertIs(caught.exception, failure)
                self.coordinator.futures[third].result(timeout=1)
        self.assertEqual(self.coordinator.records[second]["status"], "blocked")
        self.assertEqual(
            self.coordinator.records[second]["failure"],
            {"code": "dependency_incomplete", "agent_id": first},
        )
        self.assertFalse(self.coordinator.is_busy())
        for ident in (first, second, third):
            self.coordinator.resume_agent(ident)
            self.coordinator.futures[ident].result(timeout=5)
            self.assertEqual(self.coordinator.records[ident]["status"], "completed")
        self.assertEqual(self.provider.calls, 3)

    def test_paused_registry_failure_settles_future_and_remaining_workers(self):
        release, occupied = self.occupy_pool()
        first = self.coordinator.spawn_agent("Prerequisite")["agent_id"]
        second = self.coordinator.spawn_agent("Dependent", depends_on=[first])["agent_id"]
        third = self.coordinator.spawn_agent("Other dependent", depends_on=[first])["agent_id"]
        original_save = self.coordinator._save
        failure = OSError("transient registry write")
        failed = False

        def save():
            nonlocal failed
            if not failed and self.coordinator.records[second]["status"] == "paused":
                failed = True
                raise failure
            original_save()

        with patch.object(self.coordinator, "_save", side_effect=save):
            self.coordinator.request_pause()
            with self.assertRaises(OSError) as caught:
                self.coordinator.futures[second].result(timeout=1)
            self.assertIs(caught.exception, failure)
            self.coordinator.futures[third].result(timeout=1)
        self.assertEqual(self.coordinator.records[second]["status"], "paused")
        release.set()
        occupied.result(timeout=5)
        self.coordinator.futures[first].result(timeout=5)
        self.assertEqual(self.provider.calls, 0)
        self.coordinator.clear_pause()
        for ident in (first, second, third):
            self.coordinator.resume_agent(ident)
            self.coordinator.futures[ident].result(timeout=5)
            self.assertEqual(self.coordinator.records[ident]["status"], "completed")
        self.assertEqual(self.provider.calls, 3)

    def test_submission_and_registry_failures_do_not_strand_later_dependents(self):
        release, occupied = self.occupy_pool()
        first = self.coordinator.spawn_agent("Prerequisite")["agent_id"]
        second = self.coordinator.spawn_agent("Dependent", depends_on=[first])["agent_id"]
        third = self.coordinator.spawn_agent("Final dependent", depends_on=[second])["agent_id"]
        original_save = self.coordinator._save
        submission_error = RuntimeError("no thread")
        failed = False

        def save():
            nonlocal failed
            if not failed and self.coordinator.records[second]["status"] == "interrupted":
                failed = True
                raise OSError("transient registry write")
            original_save()

        with patch.object(self.coordinator.pool, "submit", side_effect=submission_error):
            with patch.object(self.coordinator, "_save", side_effect=save):
                release.set()
                occupied.result(timeout=5)
                self.coordinator.futures[first].result(timeout=5)
                with self.assertRaises(RuntimeError) as caught:
                    self.coordinator.futures[second].result(timeout=1)
                self.assertIs(caught.exception, submission_error)
                self.coordinator.futures[third].result(timeout=1)
        self.assertEqual(self.coordinator.records[second]["status"], "interrupted")
        self.assertEqual(self.coordinator.records[third]["status"], "blocked")
        for ident in (second, third):
            self.coordinator.resume_agent(ident)
            self.coordinator.futures[ident].result(timeout=5)
            self.assertEqual(self.coordinator.records[ident]["status"], "completed")
        self.assertEqual(self.provider.calls, 3)
