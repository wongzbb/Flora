# SPDX-License-Identifier: Apache-2.0
"""Explicit, structural revision of model-authored work without a semantic grader."""

import copy
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import GeneralAgent, _normalize
from flora.general.storage import atomic_json
from flora.support.errors import ValidationError
from tests.test_long_tasks import object_provider


def step(ident, **fields):
    return {
        "id": ident,
        "goal": "Inspect the selected source",
        "status": "pending",
        "required": True,
        "evidence": [],
        "note": "",
        **fields,
    }


class WorkRefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = GeneralAgent(
            session_dir=self.root / "session",
            workspace=self.root,
            provider=object_provider(),
            profile={"general": {"tool_schema_version": 4, "require_task_completion": True}},
        )
        self.app.task = {"key": "current", "task": "Read the selected source and verify the result"}
        atomic_json(self.root / "session/task.json", self.app.task)
        self.work = self.app.work
        self.work.begin("current", self.app.task["task"])
        self.anchor = self.work.read_work()["steps"][0]
        self.work.update_work([self.anchor, step("initial")], 0)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def refine(self, **changes):
        args = {
            "superseded_ids": ["initial"],
            "replacements": [step("revised")],
            "reason": "The original plan used an unsupported assumption",
            "evidence": [],
            "expected_revision": 1,
        }
        args.update(changes)
        return self.work.refine_work(**args)

    def assert_rejected_without_write(self, **changes):
        before = self.work.path.read_bytes()
        state = copy.deepcopy(self.work.state)
        with self.assertRaises(ValidationError):
            self.refine(**changes)
        self.assertEqual(self.work.path.read_bytes(), before)
        self.assertEqual(self.work.state, state)

    def test_split_merge_and_history_survive_reopen(self):
        result = self.refine(replacements=[step("left"), step("right", required=False)])
        self.assertEqual(result["history_count"], 1)
        self.assertNotIn("history", result)
        self.assertEqual(result["steps"][0], self.anchor)
        result = self.refine(
            superseded_ids=["left", "right"],
            replacements=[step("merged")],
            expected_revision=2,
        )
        history = self.work.read_work_history()
        self.assertEqual(history["entries"][0]["superseded"], [step("initial")])
        self.assertEqual(history["entries"][1]["replacement_ids"], ["merged"])
        self.assertFalse(history["claims_verified"])
        fingerprint = self.app.agent._fingerprint
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=object_provider())
        self.work = self.app.work
        self.assertEqual(self.work.read_work(), result)
        self.assertEqual(self.work.read_work_history(), history)
        self.assertEqual(self.app.agent._fingerprint, fingerprint)

    def test_invalid_refinement_is_atomic(self):
        cases = [
            {"superseded_ids": []},
            {"superseded_ids": ["initial", "initial"]},
            {"superseded_ids": ["absent"]},
            {"superseded_ids": ["task"]},
            {"superseded_ids": [False]},
            {"superseded_ids": [["initial"]]},
            {"replacements": []},
            {"replacements": [step("initial")]},
            {"replacements": [step("task")]},
            {"replacements": [step("revised", required=False)]},
            {"replacements": [step("revised", status="completed")]},
            {"replacements": [step("revised", status="blocked", note="missing")]},
            {"replacements": [step("revised"), step("revised")]},
            {"reason": " "},
            {"reason": "x" * 4001},
            {"reason": None},
            {"expected_revision": 0},
            {"expected_revision": True},
            {"evidence": [{"invented": "observation"}]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assert_rejected_without_write(**changes)

    def test_task_binding_checks_key_and_full_text_on_both_mutations(self):
        original = self.app.task.copy()
        for changes in ({"key": "next"}, {"task": "A different request"}):
            self.app.task = {**original, **changes}
            self.assert_rejected_without_write()
            with self.assertRaisesRegex(ValidationError, "another task"):
                self.work.update_work([self.anchor, step("initial")], 1)
            self.assertFalse(self.work.completion()["ready"])
        self.app.task = original

    def test_root_remains_immutable_and_pending_successor_gates_completion(self):
        for change in ({"goal": "Just read"}, {"required": False}, {"id": "fake"}):
            with self.assertRaises(ValidationError):
                self.work.update_work([{**self.anchor, **change}, step("initial")], 1)
        self.anchor["status"] = "completed"
        self.work.update_work([self.anchor, step("initial", status="completed")], 1)
        self.assertTrue(self.work.completion()["ready"])
        self.refine(expected_revision=2)
        self.assertEqual(self.work.completion()["pending_steps"], ["revised"])
        self.assertFalse(self.work.completion()["ready"])

    def test_superseded_ids_cannot_be_reintroduced(self):
        self.refine()
        self.assert_rejected_without_write(
            superseded_ids=["revised"],
            replacements=[step("initial")],
            expected_revision=2,
        )
        with self.assertRaisesRegex(ValidationError, "cannot be reused"):
            self.work.update_work([self.anchor, step("revised"), step("initial")], 2)

    def test_deleted_optional_successor_id_cannot_be_reintroduced(self):
        self.refine(replacements=[step("required_next"), step("optional_next", required=False)])
        # Successors that are still current remain editable despite occurring in history.
        self.work.update_work(
            [
                self.anchor,
                step("required_next", status="running"),
                step("optional_next", required=False, status="running"),
            ],
            2,
        )
        self.work.update_work([self.anchor, step("required_next", status="running")], 3)
        self.assert_rejected_without_write(
            superseded_ids=["required_next"],
            replacements=[step("optional_next")],
            expected_revision=4,
        )
        before = self.work.path.read_bytes()
        state = copy.deepcopy(self.work.state)
        with self.assertRaisesRegex(ValidationError, "Historical work IDs cannot be reused"):
            self.work.update_work(
                [self.anchor, step("required_next"), step("optional_next", required=False)],
                4,
            )
        self.assertEqual(self.work.path.read_bytes(), before)
        self.assertEqual(self.work.state, state)
        # Removing the optional successor does not prevent refinement with a genuinely fresh ID.
        result = self.refine(
            superseded_ids=["required_next"],
            replacements=[step("fresh")],
            expected_revision=4,
        )
        self.assertEqual(result["steps"], [self.anchor, step("fresh")])

    def test_more_than_64_cumulative_phases_and_bounded_history_pages(self):
        current = "initial"
        for revision in range(1, 101):
            successor = "phase_" + str(revision)
            result = self.refine(
                superseded_ids=[current],
                replacements=[step(successor)],
                expected_revision=revision,
            )
            self.assertEqual(len(result["steps"]), 2)
            current = successor
        self.assertEqual(result["history_count"], 100)
        first = self.work.read_work_history(limit=7)
        self.assertEqual(len(first["entries"]), 7)
        self.assertEqual(first["next_offset"], 7)
        self.assertEqual(self.work.read_work_history(offset=99)["next_offset"], None)
        self.assertEqual(self.work.read_work_history(offset=101)["entries"], [])
        first["entries"][0]["superseded"][0]["goal"] = "mutated caller copy"
        self.assertEqual(
            self.work.read_work_history(limit=1)["entries"][0]["superseded"], [step("initial")]
        )
        for args in ({"limit": 21}, {"limit": 0}, {"offset": -1}, {"offset": True}):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                self.work.read_work_history(**args)

    def test_current_step_and_storage_bounds_are_atomic(self):
        self.assert_rejected_without_write(replacements=[step("s" + str(i)) for i in range(64)])
        self.work.update_work([self.anchor, step("initial", note="x" * 4000)], 1)
        result = self.refine(
            replacements=[step("s" + str(i), note="x" * 4000) for i in range(63)],
            expected_revision=2,
        )
        self.assertEqual(len(result["steps"]), 64)
        for revision in range(3, 10):
            current = [s["id"] for s in result["steps"] if s["id"] != "task"]
            before = self.work.path.read_bytes()
            state = copy.deepcopy(self.work.state)
            try:
                result = self.refine(
                    superseded_ids=current,
                    replacements=[
                        step("s_" + str(revision) + "_" + str(i), note="x" * 4000)
                        for i in range(63)
                    ],
                    expected_revision=revision,
                )
            except ValidationError as exc:
                self.assertIn("storage bound", str(exc))
                self.assertEqual(self.work.path.read_bytes(), before)
                self.assertEqual(self.work.state, state)
                break
        else:
            self.fail("Expected bounded state to reject excessive history")

    def test_write_failure_does_not_publish_in_memory_state(self):
        before = self.work.path.read_bytes()
        state = copy.deepcopy(self.work.state)
        with patch("flora.general.work.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.refine()
        self.assertEqual(self.work.state, state)
        self.assertEqual(self.work.path.read_bytes(), before)

    def test_only_one_concurrent_revision_commits(self):
        def attempt(ident):
            try:
                self.refine(replacements=[step(ident)])
                return True
            except ValidationError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["left", "right"]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.work.read_work()["history_count"], 1)

    def test_current_evidence_checked_but_superseded_claim_is_historical(self):
        path = self.root / "source.txt"
        path.write_text("old")
        ref = {"path": "source.txt", "sha256": hashlib.sha256(b"old").hexdigest()}
        self.anchor["status"] = "completed"
        self.work.update_work([self.anchor, step("initial", status="completed", evidence=[ref])], 1)
        path.write_text("new")
        self.assertEqual(self.work.completion()["stale_evidence"], ["initial"])
        self.assert_rejected_without_write(expected_revision=2, evidence=[ref])
        fresh = {"path": "source.txt", "sha256": hashlib.sha256(b"new").hexdigest()}
        self.refine(expected_revision=2, evidence=[fresh])
        history = self.work.read_work_history()["entries"][0]
        self.assertEqual(history["superseded"][0]["evidence"], [ref])
        self.assertEqual(history["evidence"], [fresh])
        self.assertEqual(self.work.completion()["stale_evidence"], [])
        self.work.update_work(
            [self.anchor, step("revised", status="completed", evidence=[fresh])], 3
        )
        self.assertTrue(self.work.completion()["ready"])
        self.assertFalse(self.work.completion()["claims_verified"])
        path.write_text("changed again")
        self.assertEqual(self.work.completion()["stale_evidence"], ["revised"])

    def test_missing_history_loads_without_automatic_write(self):
        state = copy.deepcopy(self.work.state)
        state.pop("history")
        atomic_json(self.work.path, state)
        before = self.work.path.read_bytes()
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=object_provider())
        self.work = self.app.work
        self.assertEqual(self.work.read_work()["history_count"], 0)
        self.assertEqual(self.work.read_work_history()["entries"], [])
        self.assertEqual(self.work.path.read_bytes(), before)
        self.refine()

    def test_opt_in_tool_schemas(self):
        specs = {s["name"]: s for s in self.app.agent.tools.descriptions()}
        self.assertIn("refine_work", specs)
        self.assertIn("read_work_history", specs)
        # The compiler receives the same structural bounds the host enforces.
        schema = specs["refine_work"]["parameters"]
        self.assertEqual(schema["properties"]["replacements"]["maxItems"], 64)
        self.assertEqual(
            schema["properties"]["replacements"]["items"]["properties"]["status"]["enum"],
            ["pending", "running"],
        )
        for protocol in ("general-v1", "general-v2", "general-v3"):
            with self.assertRaises(ValidationError):
                _normalize({"general": {"protocol": protocol, "tool_schema_version": 4}})

    def test_legacy_versions_keep_original_tools_and_no_history_fields(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                directory = self.root / ("legacy_" + str(version))
                with GeneralAgent(
                    session_dir=directory,
                    workspace=self.root,
                    provider=object_provider(),
                    profile={
                        "general": {"tool_schema_version": version, "require_task_completion": True}
                    },
                ) as old:
                    old.task = {"key": "old", "task": "old task"}
                    atomic_json(directory / "task.json", old.task)
                    old.work.begin("old", "old task")
                    descriptions = old.agent.tools.descriptions()
                    fingerprint = old.agent._fingerprint
                    self.assertNotIn("refine_work", {s["name"] for s in descriptions})
                    self.assertNotIn("history_count", old.work.read_work())
                    self.assertNotIn("history", old.work.state)
                    with self.assertRaises(ValidationError):
                        old.work.refine_work(["task"], [step("new")], "reason", [], 0)
                with GeneralAgent(session_dir=directory, provider=object_provider()) as reopened:
                    self.assertEqual(reopened.agent.tools.descriptions(), descriptions)
                    self.assertEqual(reopened.agent._fingerprint, fingerprint)
        with GeneralAgent(
            session_dir=self.root / "default", workspace=self.root, provider=object_provider()
        ) as default:
            self.assertEqual(default.profile["general"]["tool_schema_version"], 3)

    def test_refinement_without_completion_gate_still_protects_required_goals(self):
        with GeneralAgent(
            session_dir=self.root / "ungated",
            workspace=self.root,
            provider=object_provider(),
            profile={"general": {"tool_schema_version": 4}},
        ) as app:
            app.task = {"key": "new", "task": "Inspect source"}
            app.work.begin("new", "Inspect source")
            self.assertEqual(app.work.read_work()["steps"], [])
            app.work.update_work([step("initial")], 0)
            with self.assertRaisesRegex(ValidationError, "immutable"):
                app.work.update_work([step("initial", goal="Skip inspection")], 1)
            result = app.work.refine_work(
                ["initial"], [step("revised")], "Revised assumption", [], 1
            )
            self.assertEqual(result["history_count"], 1)


if __name__ == "__main__":
    unittest.main()
