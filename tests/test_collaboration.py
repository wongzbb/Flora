# SPDX-License-Identifier: Apache-2.0
"""Collaboration lifecycle tests with real journals and deterministic model responses."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import GeneralAgent
from flora.integrations.providers import ModelResponse
from flora.support.errors import ValidationError
from flora.support.values import digest
from tests.helpers import bundle, pure


class WorkerProvider:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        context = json.loads(messages[1]["content"])
        tools = {t["name"] for t in context["tools"]}
        if "spawn_agent" not in tools:
            self.child_tools = tools
            value = {"finding": "Observed handoff", "handoff": context["memory"]["data"]}
        else:
            value = "Done"
        return ModelResponse(
            json.dumps(bundle(pure(value), context["epoch"], context["trace_digest"])), 11, 7
        )


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.provider = WorkerProvider()
        self.app = GeneralAgent(
            session_dir=self.root / "session",
            workspace=self.workspace,
            provider=self.provider,
            profile={"general": {"subagents": {"enabled": True, "max_children": 2}}},
        )
        self.app.task = {"key": "current", "task": "Parent research task"}
        self.app.work.begin("current", "Parent research task")

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def spawn(self, task="Look at the provided evidence", **kwargs):
        ident = self.app.delegation.spawn_agent(task, **kwargs)["agent_id"]
        self.app.delegation.futures[ident].result(timeout=5)
        return ident

    def test_collaboration_schema_rejects_known_bad_shapes_before_dispatch(self):
        from flora.language.toolcheck import validate_effect_arguments

        tools = self.app.agent.tools.descriptions()
        for tool, args in (
            ("spawn_agent", {"task": "Inspect", "context": {"selected_file": {"var": "path"}}}),
            ("read_agent", {"agent_id": {"var": "child"}, "limit": 100000}),
            ("wait_agents", {"agent_ids": [{"var": "child"}], "timeout": 61}),
            (
                "review_agent",
                {"agent_id": "x", "result_digest": "", "disposition": "done", "note": "x"},
            ),
        ):
            source = {
                "programs": [
                    {
                        "id": "main",
                        "program": {
                            "blocks": {
                                "main": {"term": {"op": "effect", "tool": tool, "args": args}}
                            }
                        },
                    }
                ]
            }
            with self.subTest(tool=tool), self.assertRaises(ValidationError):
                validate_effect_arguments(source, tools)
        source["programs"][0]["program"]["blocks"]["main"]["term"].update(
            tool="spawn_agent", args={"task": "Inspect", "context": {"guidance": {"var": "facts"}}}
        )
        validate_effect_arguments(source, tools)
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.app.delegation.records)

    def test_saved_v4_without_schema_version_keeps_its_identity(self):
        from flora.general.agent import _new_session_defaults

        def legacy(profile, **kwargs):
            _new_session_defaults(profile, **kwargs)
            profile["general"].pop("tool_schema_version", None)

        path = self.root / "legacy"
        with patch("flora.general.agent._new_session_defaults", side_effect=legacy):
            with GeneralAgent(
                session_dir=path,
                workspace=self.workspace,
                provider=WorkerProvider(),
                profile={"general": {"subagents": {"enabled": True}}},
            ) as app:
                identity = app.agent._fingerprint
                tools = app.agent.tools.descriptions()
                self.assertNotIn("tool_schema_version", app.profile["general"])
        with GeneralAgent(session_dir=path, provider=WorkerProvider()) as app:
            self.assertEqual(app.agent._fingerprint, identity)
            self.assertEqual(app.agent.tools.descriptions(), tools)
            self.assertNotIn("tool_schema_version", app.profile["general"])
        self.assertEqual(self.app.profile["general"]["tool_schema_version"], 2)

    def collect(self, ident):
        row = self.app.delegation.read_agent(ident, limit=24000)
        while row.get("next_offset") is not None:
            row = self.app.delegation.read_agent(ident, offset=row["next_offset"], limit=24000)
        return row["result_digest"]

    def test_checked_context_handoff_and_child_tool_grants(self):
        source = self.app.store.record(origin="fixture", title="Facts", text="Observed fact")
        (self.workspace / "facts.txt").write_text("real content")
        import hashlib

        sha = hashlib.sha256(b"real content").hexdigest()
        ident = self.spawn(
            context={
                "guidance": "Use supplied facts",
                "source_ids": [source["source_id"]],
                "files": [{"path": "facts.txt", "sha256": sha}],
            }
        )
        value = self.app.delegation._result_view(ident)["value"]
        data = value["handoff"]
        self.assertEqual(data["handoff"]["evidence"][0]["source_id"], source["source_id"])
        self.assertEqual(data["parent_task"], "Parent research task")
        self.assertFalse(data["claims_verified"])
        self.assertTrue(
            {"web_search", "web_fetch", "workspace_context", "read_source"}
            <= self.provider.child_tools
        )
        self.assertFalse(
            {"create_file", "update_file", "run_command", "spawn_agent", "http_request"}
            & self.provider.child_tools
        )

    def test_invented_context_is_rejected_before_worker_dispatch(self):
        with self.assertRaisesRegex(ValidationError, "Unknown source"):
            self.app.delegation.spawn_agent("research", context={"source_ids": ["src-999999"]})
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.app.delegation.records)

    def test_falsy_wrong_handoff_types_do_not_silently_become_empty(self):
        for value in ([], "", 0, False):
            with self.subTest(context=value), self.assertRaises(ValidationError):
                self.app.delegation.spawn_agent("research", context=value)
        for value in ({}, "", 0, False):
            with self.subTest(depends_on=value), self.assertRaises(ValidationError):
                self.app.delegation.spawn_agent("research", depends_on=value)
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.app.delegation.records)

    def test_queued_optional_worker_retains_original_parent_context(self):
        # Hold dispatch until a subsequent turn, without using sleeps or a race.
        from unittest.mock import patch

        with patch.object(self.app.delegation.pool, "submit"):
            ident = self.app.delegation.spawn_agent("old optional task", required=False)["agent_id"]
        self.app.task = {"key": "next", "task": "Unrelated new task"}
        self.app.work.begin("next", "Unrelated new task")
        self.app.delegation._run(ident)
        view = self.app.delegation._result_view(ident)
        self.assertEqual(view["value"]["handoff"]["parent_task"], "Parent research task")

    def test_completed_required_child_needs_collection_and_review(self):
        ident = self.spawn()
        self.assertFalse(self.app._ready_to_finish()["ready"])
        view = self.app.delegation._result_view(ident)
        with self.assertRaisesRegex(ValidationError, "Collect the complete"):
            self.app.delegation.review_agent(ident, digest(view), "accepted", "Reviewed")
        fingerprint = self.collect(ident)
        with self.assertRaises(ValidationError):
            self.app.delegation.review_agent(ident, "wrong", "accepted", "Reviewed")
        self.app.delegation.review_agent(
            ident, fingerprint, "accepted", "Collected the result; its claims remain unverified"
        )
        self.assertTrue(self.app._ready_to_finish()["ready"])
        self.assertFalse(self.app.delegation.records[ident]["review"]["claims_verified"])

    def test_disjoint_result_pages_cannot_skip_unread_middle(self):
        ident = self.spawn()
        row = self.app.delegation.read_agent(ident, limit=10)
        self.app.delegation.read_agent(ident, offset=row["total_chars"] - 10, limit=10)
        with self.assertRaisesRegex(ValidationError, "Collect the complete"):
            self.app.delegation.review_agent(
                ident, row["result_digest"], "accepted", "Skipped middle"
            )
        self.app.delegation.read_agent(ident, offset=10, limit=24000)
        self.app.delegation.review_agent(
            ident, row["result_digest"], "accepted", "Now fully collected"
        )

    def test_review_evidence_must_remain_current_until_final_return(self):
        import hashlib

        path = self.workspace / "check.txt"
        path.write_bytes(b"observed")
        ident = self.spawn()
        fingerprint = self.collect(ident)
        self.app.delegation.review_agent(
            ident,
            fingerprint,
            "accepted",
            "Checked the actual source file",
            evidence=[{"path": "check.txt", "sha256": hashlib.sha256(b"observed").hexdigest()}],
        )
        self.assertTrue(self.app.delegation.completion()["ready"])
        path.write_bytes(b"changed after review")
        result = self.app.delegation.completion()
        self.assertFalse(result["ready"])
        self.assertEqual(result["stale_review_evidence"], [ident])

    def test_dedup_does_not_charge_or_start_another_worker(self):
        ident = self.spawn()
        before = self.provider.calls
        duplicate = self.app.delegation.spawn_agent("Look at the provided evidence")
        self.assertEqual(duplicate["agent_id"], ident)
        self.assertTrue(duplicate["reused"])
        self.assertEqual(self.provider.calls, before)

    def test_quota_is_per_task_and_old_workers_not_dependencies(self):
        first = self.spawn("First")
        self.spawn("Second")
        with self.assertRaisesRegex(ValidationError, "quota"):
            self.app.delegation.spawn_agent("Third")
        self.app.task = {"key": "next", "task": "Next turn"}
        self.app.work.begin("next", "Next turn")
        self.spawn("Third")
        with self.assertRaisesRegex(ValidationError, "current parent task"):
            self.app.delegation.spawn_agent("dependent", depends_on=[first])
        with self.assertRaisesRegex(ValidationError, "original parent task"):
            self.app.delegation.resume_agent(first)

    def test_dependency_handoff_uses_actual_answer(self):
        first = self.spawn("Independent fact")
        second = self.spawn("Consume dependency", depends_on=[first])
        deps = self.app.delegation._result_view(second)["value"]["handoff"]["dependencies"]
        self.assertEqual(deps[0]["agent_id"], first)
        self.assertEqual(deps[0]["result"]["value"]["finding"], "Observed handoff")
        self.assertFalse(deps[0]["claims_verified"])

    def test_failed_dependency_is_blocked_without_model_or_hidden_retry(self):
        first = self.spawn("Independent fact")
        self.app.delegation.records[first]["status"] = "needs_program"
        calls = self.provider.calls
        second = self.spawn("Consume dependency", depends_on=[first])
        self.assertEqual(self.provider.calls, calls)
        self.assertEqual(self.app.delegation.records[second]["status"], "blocked")
        self.app.delegation.read_agent(second)
        with self.assertRaises(ValidationError):
            self.app.delegation.review_agent(second, "", "accepted", "Cannot accept unfinished")
        self.app.delegation.review_agent(
            second, "", "blocked", "The actual dependency failed; report this limitation"
        )

    def test_unknown_outcome_is_never_resumed_as_retry(self):
        ident = self.spawn()
        self.app.delegation.records[ident]["status"] = "interrupted_unknown"
        calls = self.provider.calls
        with self.assertRaisesRegex(ValidationError, "external evidence"):
            self.app.delegation.resume_agent(ident)
        self.assertEqual(self.provider.calls, calls)

    def test_saved_registry_restores_reviews_and_budget(self):
        ident = self.spawn()
        fingerprint = self.collect(ident)
        self.app.delegation.review_agent(ident, fingerprint, "accepted", "Collected")
        profile = copy.deepcopy(self.app.profile)
        budget = self.app.delegation.records[ident]["budget"]
        self.app.close()
        self.app = GeneralAgent(session_dir=self.root / "session", provider=self.provider)
        self.assertEqual(self.app.profile, profile)
        self.assertEqual(self.app.delegation.records[ident]["budget"], budget)
        self.assertEqual(self.app.delegation.records[ident]["review"]["result_digest"], fingerprint)

    def test_v3_saved_child_identity_is_not_upgraded(self):
        old_dir = self.root / "old"
        profile = {"general": {"protocol": "general-v3", "subagents": {"enabled": True}}}
        with GeneralAgent(
            session_dir=old_dir, workspace=self.workspace, provider=self.provider, profile=profile
        ) as old:
            descriptions, fingerprint = old.agent.tools.descriptions(), old.agent._fingerprint
            self.assertNotIn("review_agent", {t["name"] for t in descriptions})
        with GeneralAgent(session_dir=old_dir, provider=self.provider) as old:
            self.assertEqual(old.agent._fingerprint, fingerprint)
            self.assertEqual(old.agent.tools.descriptions(), descriptions)
