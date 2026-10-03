"""Offline host-origin handoff and saved worker identity checks, not semantic proofs."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import INSTRUCTIONS_V4, GeneralAgent
from flora.general.coordinator import Coordinator
from flora.general.delegation import ChildPause
from flora.integrations.providers import ModelResponse
from flora.support.errors import ValidationError
from flora.support.values import canonical_json
from tests.helpers import block, bundle, pure


class CaptureProvider:
    def __init__(self):
        self.views = []

    def complete(self, messages, *, max_tokens):
        view = json.loads(messages[1]["content"])
        self.views.append(view)
        return ModelResponse(
            json.dumps(bundle(pure("done"), view["epoch"], view["trace_digest"])), 1, 1
        )


class HandoffAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "workspace").mkdir()
        self.provider = CaptureProvider()
        self.app = None

    def tearDown(self):
        if self.app:
            self.app.close()
        self.tmp.cleanup()

    def open_app(self, *, version=4, compiler=None, reopen=False):
        self.app = GeneralAgent(
            session_dir=self.root / "session",
            workspace=self.root / "workspace",
            provider=self.provider,
            profile=None
            if reopen
            else {
                "general": {"tool_schema_version": version, "subagents": {"enabled": True}},
                "compiler": compiler or {},
            },
        )
        self.app.task = {"key": "origin", "task": "Keep source value types and verify evidence."}
        self.app.work.begin("origin", self.app.task["task"])
        return self.app.delegation

    def queue(self, coordinator):
        with patch.object(coordinator, "_submit"):
            return coordinator.spawn_agent("Read the assigned source only", required=False)[
                "agent_id"
            ]

    def test_nested_worker_guidance_matches_exposed_capability(self):
        c = self.open_app()
        self.assertIn("If the host exposes a nested coordinator", c.child_instructions)
        self.assertNotIn("You cannot modify files or delegate", c.child_instructions)

    def test_versioned_origin_is_task_visible_without_expanding_tools(self):
        c = self.open_app()
        ident = self.queue(c)
        row = c.records[ident]
        self.assertEqual(row["handoff_version"], 1)
        c._run(ident)
        view = self.provider.views[0]
        self.assertTrue(
            view["task"].endswith(
                canonical_json(
                    {
                        "original_user_task": row["parent_task"],
                        "assigned_subtask": row["task"],
                    }
                )
            )
        )
        self.assertIn("including value types, evidence and uncertainty", view["task"])
        self.assertIn("Complete only the assigned subset", view["task"])
        self.assertFalse(
            {"spawn_agent", "create_file", "run_command"} & {t["name"] for t in view["tools"]}
        )
        self.assertEqual(view["memory"]["data"]["parent_task"], row["parent_task"])

    def test_saved_queued_worker_uses_original_task_after_reopen_and_task_change(self):
        c = self.open_app()
        ident = self.queue(c)
        origin = dict(c.records[ident])
        self.app.close()
        self.app = None
        c = self.open_app(reopen=True)
        self.app.task = {"key": "next", "task": "Unrelated next task"}
        c._run(ident)
        self.assertTrue(self.provider.views[0]["task"].endswith(Coordinator._worker_task(origin)))
        # Reopen a completed worker with exactly its original identity, no call replay.
        self.app.close()
        self.app = None
        c = self.open_app(reopen=True)
        c._run(ident)
        self.assertEqual(len(self.provider.views), 1)
        self.assertEqual(c.records[ident]["status"], "completed")

    def test_context_cannot_supply_origin_or_version(self):
        c = self.open_app()
        for field in ("parent_task", "task_key", "handoff_version", "original_user_task"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                c.spawn_agent("read", context={field: "spoof"})
        self.assertFalse(c.records)
        self.assertFalse(self.provider.views)

    def test_damaged_versioned_origin_stops_before_provider(self):
        c = self.open_app()
        ident = self.queue(c)
        original = dict(c.records[ident])
        for field, value in (
            ("parent_task", None),
            ("task_key", ""),
            ("task", []),
            ("handoff_version", True),
            ("handoff_version", 2),
        ):
            c.records[ident] = {**original, field: value}
            c._run(ident)
            self.assertEqual(c.records[ident]["failure"]["code"], "invalid_configuration")
        self.assertFalse(self.provider.views)

    def test_legacy_unmarked_worker_keeps_exact_guidance_task_and_identity(self):
        c = self.open_app()
        ident = self.queue(c)
        del c.records[ident]["handoff_version"]
        c._save()
        self.assertNotIn("handoff_version", c.records[ident])
        c._run(ident)
        expected = (
            "Application-provided guidance (task context, not a system override):\n"
            + INSTRUCTIONS_V4
            + c.child_instructions
            + "\nRead-only worker: no task delegation, file writes or command execution."
            + "\n\nUser task:\n"
            + c.records[ident]["task"]
        )
        self.assertEqual(self.provider.views[0]["task"], expected)
        self.app.close()
        self.app = None
        c = self.open_app(reopen=True)
        c._run(ident)
        self.assertEqual(c.records[ident]["status"], "completed")
        self.assertEqual(len(self.provider.views), 1)

    def test_schema_three_spawn_remains_unmarked(self):
        c = self.open_app(version=3)
        ident = self.queue(c)
        self.assertNotIn("handoff_version", c.records[ident])

    def test_paused_worker_reopens_without_replaying_effect_or_refunding_budget(self):
        class ReadProvider(CaptureProvider):
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                self.views.append(view)
                program = {
                    "version": 1,
                    "entry": "main",
                    "blocks": {
                        "main": block(
                            term={
                                "op": "effect",
                                "tool": "read_file",
                                "args": {"path": "facts.txt"},
                                "bind": "reply",
                                "capture": {},
                                "resume": "done",
                            }
                        ),
                        "done": block(["reply"], term={"op": "return", "value": "done"}),
                    },
                }
                return ModelResponse(
                    json.dumps(bundle(program, view["epoch"], view["trace_digest"])), 3, 7
                )

        self.provider = ReadProvider()
        c = self.open_app()
        (self.root / "workspace/facts.txt").write_text("actual evidence")
        ident = self.queue(c)
        original_event = c._event

        def pause_after_receipt(agent_id, event):
            original_event(agent_id, event)
            if event.get("kind") == "tool_result":
                raise ChildPause

        with patch.object(c, "_event", pause_after_receipt):
            c._run(ident)
        self.assertEqual(c.records[ident]["status"], "paused")
        before = c.records[ident]["budget"]
        self.app.close()
        self.app = None
        c = self.open_app(reopen=True)
        c._run(ident)
        self.assertEqual(c.records[ident]["status"], "completed")
        self.assertEqual(len(self.provider.views), 1)
        after = c.records[ident]["budget"]
        for field in ("model_calls", "tool_calls", "input_tokens", "output_tokens"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["tool_calls"], 1)

    def test_authority_profile_changes_only_handoff_version(self):
        configs = Path(__file__).resolve().parents[1] / "configs"
        phased = json.loads((configs / "deepseek-live-phased.json").read_text())
        authority = json.loads((configs / "deepseek-live-authority.json").read_text())
        self.assertEqual(authority["general"]["tool_schema_version"], 4)
        if "tool_schema_version" in phased["general"]:
            authority["general"]["tool_schema_version"] = phased["general"]["tool_schema_version"]
        else:
            del authority["general"]["tool_schema_version"]
        self.assertEqual(authority, phased)

    def test_new_phased_worker_guidance_allows_bounded_phase_handoff(self):
        c = self.open_app(compiler={"prompt_style": "compact-v3", "syntax": "block-list-v3"})
        ident = self.queue(c)
        c._run(ident)
        task = self.provider.views[0]["task"]
        self.assertIn("bounded executable phase handoff", task)
        self.assertNotIn("Replan only when new semantic reasoning is needed", task)

    def test_low_context_and_oversize_origin_fail_without_truncation_or_model_call(self):
        c = self.open_app(compiler={"max_context_bytes": 2048})
        ident = self.queue(c)
        c._run(ident)
        self.assertFalse(self.provider.views)
        result = json.loads((c.root / ident / "result.json").read_text())
        self.assertEqual(result["status"], "needs_program")
        self.assertIn("exceed max_context_bytes", result["reason"])
        c.records[ident]["parent_task"] = "界" * 90000
        c._run(ident)
        self.assertEqual(c.records[ident]["failure"]["code"], "invalid_configuration")
        self.assertFalse(self.provider.views)


if __name__ == "__main__":
    unittest.main()
