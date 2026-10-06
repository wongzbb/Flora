# SPDX-License-Identifier: Apache-2.0
"""Collaboration lifecycle tests with real journals and deterministic model responses."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import GeneralAgent
from flora.general.coordinator import _review_value_projection
from flora.general.delegation import _child_provider
from flora.integrations.providers import ModelResponse, OpenAICompatibleProvider
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
        self.child_tools = tools
        if "spawn_agent" not in tools:
            value = {"finding": "Observed handoff", "handoff": context["memory"]["data"]}
        else:
            value = "Done"
        return ModelResponse(
            json.dumps(bundle(pure(value), context["epoch"], context["trace_digest"])), 11, 7
        )


class CollaborationTests(unittest.TestCase):
    def test_review_projection_is_bounded_and_marks_omitted_values(self):
        small = _review_value_projection({"status": "completed", "value": {"answer": 4}}, "failed")
        self.assertEqual(small["child_value"], {"answer": 4})
        self.assertFalse(small["child_value_omitted"])
        large = _review_value_projection({"status": "completed", "value": "x" * 40000}, "failed")
        self.assertTrue(large["result_available"])
        self.assertIsNone(large["child_value"])
        self.assertTrue(large["child_value_omitted"])
        self.assertEqual(len(large["child_value_digest"]), 64)

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

    def test_child_result_descriptions_preserve_actual_object_shapes(self):
        descriptions = {spec.name: spec.description for spec in self.app.delegation._tools()}
        self.assertIn("entries:", descriptions["list_files"])
        self.assertIn("sources:", descriptions["list_sources"])
        self.assertIn("skills:", descriptions["list_skills"])
        self.assertIn("not a generic capabilities array", descriptions["child_capabilities"])
        self.assertIn("workspace_root", descriptions["workspace_context"])
        host_descriptions = {
            item["name"]: item["description"] for item in self.app.agent.tools.descriptions()
        }
        self.assertIn("authoritative collection/contract observation", host_descriptions["review_agent"])
        self.assertIn("include context.contract before spawning", host_descriptions["spawn_agent"])
        self.assertIn("complete observed child read view", host_descriptions["collect_agent"])
        self.assertIn("outcome targets minimal", host_descriptions["collect_agent"])
        self.assertIn("complete observed child read view", host_descriptions["collect_completed_agent"])
        self.assertIn("one complete observed child read view per requested ID", host_descriptions["collect_completed_agents"])
        self.assertIn("one host review observation per requested child", host_descriptions["review_agents"])
        self.assertIn("may omit result_digest", host_descriptions["review_agents"])
        self.assertIn("replaces", host_descriptions["spawn_agent"])
        self.assertIn("explicit replacement lineage", host_descriptions["spawn_agents"])
        self.assertIn("per-child errors", host_descriptions["resume_agents"])

    def test_delegation_guidance_does_not_contradict_exposed_nested_coordinator(self):
        self.assertIn("Recursive delegation is", self.app.delegation.instructions)
        self.assertIn("nested coordinator", self.app.delegation.instructions)
        self.assertNotIn("Children cannot", self.app.delegation.instructions)

    def test_contract_guidance_separates_process_obligations_from_source_evidence(self):
        self.assertIn("host-checkable file_read/source_read", self.app.delegation.instructions)
        self.assertIn("leave evidence_requirements empty", self.app.delegation.instructions)
        from flora.general.coordinator import Coordinator

        self.assertIn("free-form evidence string remains UNKNOWN", Coordinator.authority_instructions)
        self.assertIn("never a child value", Coordinator.authority_instructions)

    def test_nested_provider_idle_guard_respects_first_program_window(self):
        provider = OpenAICompatibleProvider(
            base_url="https://example.test/v1",
            model="test",
            api_key_env=None,
            timeout=20,
            total_timeout=300,
            progress_timeout=60,
            first_program_timeout=240,
        )
        child = _child_provider(provider)
        self.assertIsNot(child, provider)
        self.assertEqual(child.progress_timeout, 120)
        self.assertEqual(provider.progress_timeout, 60)

    def test_nested_provider_does_not_exceed_total_timeout(self):
        provider = OpenAICompatibleProvider(
            base_url="https://example.test/v1",
            model="test",
            api_key_env=None,
            timeout=20,
            total_timeout=90,
            progress_timeout=30,
            first_program_timeout=240,
        )
        self.assertEqual(_child_provider(provider).progress_timeout, 90)

    def test_nested_tools_require_a_local_delegation_contract(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "uncontracted-nested",
        )
        try:
            ident = coord.spawn_agent("Complete this leaf without delegation")["agent_id"]
            coord.futures[ident].result(timeout=5)
            self.assertNotIn("spawn_agent", self.provider.child_tools)
        finally:
            coord.close()

    def test_batch_retry_reuses_existing_ids_without_quota_failure(self):
        specs = [
            {"task": "Independent A", "name": "a"},
            {"task": "Independent B", "name": "b"},
        ]
        first = self.app.delegation.spawn_agents(specs)
        for ident in first["agent_ids"]:
            self.app.delegation.futures[ident].result(timeout=5)
        second = self.app.delegation.spawn_agents(specs)
        self.assertEqual(second["agent_ids"], first["agent_ids"])
        self.assertEqual(len(self.app.delegation.records), 2)

    def test_batch_duplicate_dependencies_are_normalized_before_admission(self):
        dependency = self.spawn("Dependency")
        before = set(self.app.delegation.records)
        with self.assertRaises(ValidationError):
            self.app.delegation.spawn_agents(
                [
                    {"task": "Same dependent task", "depends_on": [dependency]},
                    {
                        "task": "Same dependent task",
                        "depends_on": [{"agent_id": dependency}, dependency],
                    },
                ]
            )
        self.assertEqual(set(self.app.delegation.records), before)

    def test_batch_validation_completes_before_any_child_is_started(self):
        with self.assertRaises(ValidationError):
            self.app.delegation.spawn_agents(
                [
                    {"task": "Valid first item"},
                    {"task": "Invalid later item", "context": {"source_ids": "bad"}},
                ]
            )
        self.assertEqual(self.app.delegation.records, {})

    def test_collaboration_schema_rejects_known_bad_shapes_before_dispatch(self):
        from flora.language.toolcheck import validate_effect_arguments

        tools = self.app.agent.tools.descriptions()
        for tool, args in (
            ("spawn_agent", {"task": "Inspect", "context": {"selected_file": {"var": "path"}}}),
            ("read_agent", {"agent_id": {"var": "child"}, "limit": 100000}),
            ("wait_agents", {"agent_ids": [{"var": "child"}], "timeout": 301}),
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

    def test_batch_spawn_starts_each_model_authored_worker_and_keeps_review_gate(self):
        rows = self.app.delegation.spawn_agents(
            [
                {"task": "Inspect the first supplied fact", "name": "First"},
                {"task": "Inspect the second supplied fact", "name": "Second"},
            ]
        )
        self.assertEqual(rows["count"], 2)
        ids = [row["agent_id"] for row in rows["agents"]]
        self.assertEqual(rows["agent_ids"], ids)
        for ident in ids:
            self.app.delegation.futures[ident].result(timeout=5)
        self.assertEqual({self.app.delegation.records[i]["status"] for i in ids}, {"completed"})
        self.assertEqual(self.app.delegation.completion()["unreviewed_workers"], ids)
        descriptions = {item["name"] for item in self.app.agent.tools.descriptions()}
        self.assertIn("spawn_agents", descriptions)

    def test_completion_exposes_host_derived_worker_observations(self):
        ident = self.spawn("Observed child")
        fingerprint = self.collect(ident)
        self.app.delegation.review_agent(
            ident, fingerprint, "accepted", "Collected and reviewed the child result"
        )
        observed = self.app.delegation.completion()["observed_workers"]
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["agent_id"], ident)
        self.assertEqual(observed[0]["status"], "completed")
        self.assertTrue(observed[0]["result_available"])
        self.assertEqual(observed[0]["review_disposition"], "accepted")
        self.assertEqual(len(observed[0]["result_digest"]), 64)
        self.assertFalse(observed[0]["claims_verified"])

    def test_batch_spawn_rejects_duplicate_handoffs_before_side_effects(self):
        with self.assertRaisesRegex(ValidationError, "duplicate task/context/dependency"):
            self.app.delegation.spawn_agents(
                [{"task": "Produce an independent calculation"}] * 2
            )
        self.assertEqual(self.app.delegation.records, {})

    def test_spawn_result_envelope_can_be_passed_to_single_agent_tools(self):
        envelope = self.app.delegation.spawn_agent("Inspect one result")
        ident = envelope["agent_id"]
        self.app.delegation.futures[ident].result(timeout=5)
        row = self.app.delegation.read_agent(envelope, limit=24000)
        self.assertEqual(row["agent_id"], ident)
        with self.assertRaises(ValidationError):
            self.app.delegation.read_agent({"status": "completed"})

    def test_batch_read_preserves_individual_result_digests(self):
        rows = self.app.delegation.spawn_agents(
            [{"task": "first"}, {"task": "second"}]
        )
        ids = rows["agent_ids"]
        for ident in ids:
            self.app.delegation.futures[ident].result(timeout=5)
        batch = self.app.delegation.read_agents(rows["agents"], limit=24000)
        self.assertEqual([item["agent_id"] for item in batch["agents"]], ids)
        self.assertTrue(all(item["result_available"] for item in batch["agents"]))
        self.assertTrue(all(item["total_chars"] > 0 for item in batch["agents"]))

    def test_collect_agent_assembles_pages_without_bypassing_review(self):
        ident = self.spawn("collect a paginated result")
        collected = self.app.delegation.collect_agent(ident, limit=1)
        self.assertTrue(collected["result_available"])
        self.assertIsNone(collected["next_offset"])
        self.assertEqual(collected["result"]["status"], "completed")
        self.assertEqual(collected["child_status"], "completed")
        self.assertEqual(collected["child_value"]["finding"], "Observed handoff")
        reviewed = self.app.delegation.review_agent(
            ident,
            collected["result_digest"],
            "accepted",
            "Collected every page and inspected the complete observed result.",
        )
        self.assertEqual(reviewed["review"]["disposition"], "accepted")
        self.assertEqual(reviewed["disposition"], "accepted")
        self.assertEqual(reviewed["contract_status"], "not_applicable")
        self.assertEqual(reviewed["result_digest"], collected["result_digest"])
        self.assertTrue(reviewed["result_available"])
        self.assertEqual(reviewed["child_status"], "completed")
        self.assertEqual(reviewed["child_value"], collected["child_value"])
        self.assertFalse(reviewed["claims_verified"])

    def test_collect_completed_agent_waits_then_collects_without_acceptance(self):
        ident = self.app.delegation.spawn_agent("wait and collect") ["agent_id"]
        collected = self.app.delegation.collect_completed_agent(ident, timeout=5, limit=1)
        self.assertTrue(collected["result_available"])
        self.assertIsNone(collected["next_offset"])
        self.assertIsNone(self.app.delegation.records[ident].get("review"))
        self.assertEqual(collected["child_status"], "completed")
        self.assertIsInstance(collected["child_value"], dict)

    def test_collect_completed_agents_batches_wait_without_bypassing_review(self):
        rows = self.app.delegation.spawn_agents(
            [{"task": "batch wait one"}, {"task": "batch wait two"}]
        )
        collected = self.app.delegation.collect_completed_agents(
            rows["agent_ids"], timeout=5, limit=1
        )
        self.assertEqual([row["agent_id"] for row in collected["agents"]], rows["agent_ids"])
        self.assertTrue(all(row["result_available"] for row in collected["agents"]))
        self.assertTrue(all(row["next_offset"] is None for row in collected["agents"]))
        self.assertTrue(all(self.app.delegation.records[i].get("review") is None for i in rows["agent_ids"]))

    def test_resume_agents_preserves_each_child_boundary(self):
        rows = self.app.delegation.spawn_agents(
            [{"task": "resume batch one"}, {"task": "resume batch two"}]
        )
        for ident in rows["agent_ids"]:
            self.app.delegation.futures[ident].result(timeout=5)
        resumed = self.app.delegation.resume_agents(rows["agent_ids"])
        self.assertEqual([item["agent_id"] for item in resumed["agents"]], rows["agent_ids"])
        self.assertTrue(all(item["status"] == "completed" for item in resumed["agents"]))
        self.assertTrue(all(self.app.delegation.records[i].get("review") is None for i in rows["agent_ids"]))

    def test_batch_reviews_keep_individual_digests_and_expose_partial_failure(self):
        rows = self.app.delegation.spawn_agents(
            [{"task": "review batch one"}, {"task": "review batch two"}]
        )
        for ident in rows["agent_ids"]:
            self.app.delegation.futures[ident].result(timeout=5)
        collected = self.app.delegation.collect_completed_agents(rows["agent_ids"], timeout=0)
        reviews = [
            {
                "agent_id": collected["agents"][0]["agent_id"],
                "result_digest": "wrong-digest",
                "disposition": "accepted",
                "note": "The host should reject this stale digest.",
            },
            {
                "agent_id": collected["agents"][1]["agent_id"],
                "result_digest": collected["agents"][1]["result_digest"],
                "disposition": "accepted",
                "note": "Collected and checked the complete observed result.",
            },
        ]
        outcome = self.app.delegation.review_agents(reviews)
        self.assertFalse(outcome["all_reviewed"])
        self.assertFalse(outcome["all_accepted"])
        self.assertEqual(outcome["reviews"][0]["status"], "error")
        self.assertEqual(outcome["reviews"][1]["status"], "reviewed")
        self.assertEqual(outcome["reviews"][1]["disposition"], "accepted")
        self.assertEqual(outcome["reviews"][1]["child_status"], "completed")
        self.assertIn("child_value", outcome["reviews"][1])
        self.assertFalse(outcome["claims_verified"])
        self.assertIn(rows["agent_ids"][0], self.app.delegation.completion()["unreviewed_workers"])

    def test_batch_review_can_bind_current_digest_without_copying_it(self):
        ident = self.spawn("review without digest copy")
        collected = self.app.delegation.collect_completed_agent(ident, timeout=5, limit=1)
        outcome = self.app.delegation.review_agents([{
            "agent_id": ident,
            "disposition": "accepted",
            "note": "The complete current result is available at the host boundary.",
        }])
        self.assertTrue(outcome["all_reviewed"])
        self.assertTrue(outcome["all_accepted"])
        reviewed = outcome["reviews"][0]
        self.assertEqual(reviewed["result_digest"], collected["result_digest"])
        self.assertEqual(reviewed["review"]["result_digest_source"], "host_current_result")
        self.assertEqual(
            self.app.delegation.records[ident]["review"]["result_digest_source"],
            "host_current_result",
        )

    def test_nested_identity_envelopes_are_unwrapped_only_at_collaboration_boundary(self):
        row = self.app.delegation.spawn_agent("nested identity")
        ident = row["agent_id"]
        self.app.delegation.futures[ident].result(timeout=5)
        nested = {"agent_id": {"agent_id": ident, "name": "untrusted"}}
        observed = self.app.delegation.read_agents([nested], limit=24000)
        self.assertEqual(observed["agents"][0]["agent_id"], ident)

    def test_wait_agents_uses_the_same_flat_read_view_as_read_agent(self):
        row = self.app.delegation.spawn_agent("flat wait view")
        ident = row["agent_id"]
        self.app.delegation.futures[ident].result(timeout=5)
        waited = self.app.delegation.wait_agents([ident], timeout=0)["agents"][0]
        read = self.app.delegation.read_agent(ident, limit=24000)
        self.assertEqual(waited["agent_id"], read["agent_id"])
        self.assertEqual(waited["text"], read["text"])
        self.assertEqual(waited.get("result"), read.get("result"))

    def test_nested_coordinator_is_available_only_with_a_bounded_depth(self):
        root = self.root / "nested-session"
        with GeneralAgent(
            session_dir=root,
            workspace=self.workspace,
            provider=self.provider,
            profile={
                "general": {
                    "subagents": {"enabled": True, "max_children": 2, "max_depth": 2}
                }
            },
        ) as app:
            app.task = {"key": "nested", "task": "Nested parent task"}
            app.work.begin("nested", "Nested parent task")
            ident = app.delegation.spawn_agent(
                "Inspect one branch",
                context={"contract": {"delegation": {"min_children": 0, "max_children": 1}}},
            )["agent_id"]
            app.delegation.futures[ident].result(timeout=5)
            self.assertIn("spawn_agent", self.provider.child_tools)
            self.assertTrue((root / "subagents" / ident / "subagents" / "children.json").exists())
            self.assertEqual(app.delegation.capabilities()["max_depth"], 2)

    def test_shared_nested_budget_stops_cross_level_fanout(self):
        with GeneralAgent(
            session_dir=self.root / "budget-session",
            workspace=self.workspace,
            provider=self.provider,
            profile={
                "general": {
                    "subagents": {
                        "enabled": True,
                        "max_children": 2,
                        "max_depth": 2,
                        "max_total_children": 1,
                    }
                }
            },
        ) as app:
            app.task = {"key": "budget", "task": "Bounded nested task"}
            app.work.begin("budget", "Bounded nested task")
            app.delegation.spawn_agent("First")
            with self.assertRaisesRegex(ValidationError, "Nested subagent budget"):
                app.delegation.spawn_agent("Second")

    def test_total_child_admission_survives_session_restart(self):
        path = self.root / "durable-budget"
        profile = {
            "general": {
                "subagents": {
                    "enabled": True,
                    "max_children": 2,
                    "max_total_children": 1,
                }
            }
        }
        first = GeneralAgent(
            session_dir=path,
            workspace=self.workspace,
            provider=self.provider,
            profile=profile,
        )
        first.task = {"key": "durable", "task": "Durable child budget"}
        first.work.begin("durable", "Durable child budget")
        ident = first.delegation.spawn_agent("First")['agent_id']
        first.delegation.futures[ident].result(timeout=5)
        saved_profile = copy.deepcopy(first.profile)
        first.close()
        with GeneralAgent(
            session_dir=path,
            provider=self.provider,
            profile=saved_profile,
        ) as reopened:
            with self.assertRaisesRegex(ValidationError, "Nested subagent budget"):
                reopened.delegation.spawn_agent("Second")

    def test_structured_evidence_requirement_cannot_be_satisfied_by_child_claim(self):
        contract = {
            "outputs": {"finding": {"type": "string"}},
            "evidence_requirements": [
                {"kind": "file_read", "path": "facts.txt", "complete": True}
            ],
        }
        ident = self.app.delegation.spawn_agent(
            "Return a finding without reading a file", context={"contract": contract}
        )["agent_id"]
        self.app.delegation.futures[ident].result(timeout=5)
        page = self.app.delegation.read_agent(ident, limit=24000)
        with self.assertRaisesRegex(ValidationError, "evidence requirement"):
            self.app.delegation.review_agent(
                ident, page["result_digest"], "accepted", "The answer was collected"
            )

    def test_evidence_witnesses_come_from_child_trace_receipts(self):
        ident = self.app.delegation.spawn_agent("Trace witness")['agent_id']
        self.app.delegation.futures[ident].result(timeout=5)
        from flora.state.trace import GENESIS, SQLiteTrace

        path = self.app.delegation.root / ident / "kernel" / "turn-99999999.sqlite"
        trace = SQLiteTrace(path)
        event = trace.begin("read_file", {"path": "facts.txt"}, expected_epoch=0, expected_digest=GENESIS)
        trace.settle(
            event,
            {
                "status": "returned",
                "value": {
                    "path": "facts.txt",
                    "sha256": "a" * 64,
                    "offset": 0,
                    "has_more": False,
                    "truncated": False,
                },
            },
        )
        trace.close()
        self.assertEqual(
            self.app.delegation._child_evidence_witnesses(ident),
            [{"kind": "file_read", "path": "facts.txt", "sha256": "a" * 64, "complete": True}],
        )

    def test_tail_only_file_receipt_is_not_complete_evidence(self):
        ident = self.spawn("Trace partial witness")
        from flora.state.trace import GENESIS, SQLiteTrace

        path = self.app.delegation.root / ident / "kernel" / "turn-99999998.sqlite"
        trace = SQLiteTrace(path)
        event = trace.begin("read_file", {"path": "facts.txt", "offset": 9}, expected_epoch=0, expected_digest=GENESIS)
        trace.settle(
            event,
            {
                "status": "returned",
                "value": {
                    "path": "facts.txt",
                    "sha256": "a" * 64,
                    "offset": 9,
                    "has_more": False,
                    "truncated": True,
                },
            },
        )
        trace.close()
        witness = self.app.delegation._child_evidence_witnesses(ident)
        self.assertEqual(witness[-1]["complete"], False)

    def test_textual_evidence_requirement_is_unknown_not_pass(self):
        observed = self.app.delegation._evidence_observation(
            {"evidence_requirements": ["read the complete source"]}, []
        )
        self.assertEqual(observed["status"], "unknown")
        self.assertEqual(observed["required"], 1)
        self.assertEqual(observed["unknown"], ["read the complete source"])

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
        self.assertEqual(self.app.profile["general"]["tool_schema_version"], 3)

    def collect(self, ident):
        row = self.app.delegation.read_agent(ident, limit=24000)
        while row.get("next_offset") is not None:
            row = self.app.delegation.read_agent(ident, offset=row["next_offset"], limit=24000)
        return row["result_digest"]

    def test_structured_result_is_only_in_a_complete_window_and_stays_unverified(self):
        ident = self.spawn()
        small = self.app.delegation.read_agent(ident, limit=1)
        self.assertNotIn("result", small)
        row = self.app.delegation.read_agent(ident, limit=24000)
        self.assertEqual(row["result"], json.loads(row["text"]))
        self.assertFalse(row["result"]["claims_verified"])
        row["result"]["value"] = "mutated caller copy"
        again = self.app.delegation.read_agent(ident, limit=24000)
        self.assertNotEqual(again["result"]["value"], row["result"]["value"])
        self.assertIsNone(self.app.delegation.records[ident].get("review"))

    def test_saved_schema_v2_retains_tools_and_text_result_shape(self):
        path = self.root / "schema-v2"
        with GeneralAgent(
            session_dir=path,
            workspace=self.workspace,
            provider=WorkerProvider(),
            profile={"general": {"tool_schema_version": 2, "subagents": {"enabled": True}}},
        ) as app:
            app.task = {"key": "current", "task": "test"}
            app.work.begin("current", "test")
            ident = app.delegation.spawn_agent("Read supplied evidence")["agent_id"]
            app.delegation.futures[ident].result(timeout=5)
            identity, descriptions = app.agent._fingerprint, app.agent.tools.descriptions()
            self.assertNotIn("result", app.delegation.read_agent(ident, limit=24000))
        with GeneralAgent(session_dir=path, provider=WorkerProvider()) as app:
            self.assertEqual(app.agent._fingerprint, identity)
            self.assertEqual(app.agent.tools.descriptions(), descriptions)
            self.assertNotIn("result", app.delegation.read_agent(ident, limit=24000))

    def test_table_types_describe_source_before_filter_without_coercing_values(self):
        (self.workspace / "typed.csv").write_text("flag,n\ntrue,1\nfalse,2\n")
        empty = self.app.documents.table_query(
            "typed.csv", filters=[{"column": "flag", "op": "eq", "value": True}]
        )
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["cell_types"], {"flag": ["string"], "n": ["string"]})
        selected = self.app.documents.table_query(
            "typed.csv", filters=[{"column": "flag", "op": "eq", "value": "true"}]
        )
        self.assertEqual(selected["rows"], [{"flag": "true", "n": "1"}])

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
                "contract": {
                    "assumptions": ["facts.txt is UTF-8 text"],
                    "inputs": {"kind": "text"},
                    "outputs": {"finding": "string"},
                    "guarantees": ["preserve observed text as a string"],
                    "dependencies": ["facts.txt"],
                    "evidence_requirements": ["read the complete file before reporting"],
                },
            }
        )
        value = self.app.delegation._result_view(ident)["value"]
        data = value["handoff"]
        self.assertEqual(data["handoff"]["evidence"][0]["source_id"], source["source_id"])
        self.assertEqual(
            data["handoff"]["contract"]["guarantees"],
            ["preserve observed text as a string"],
        )
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

    def test_contract_type_violation_blocks_accepted_review(self):
        ident = self.spawn(
            context={"contract": {"outputs": {"result": "number"}}}
        )
        fingerprint = self.collect(ident)
        observed = self.app.delegation.read_agent(ident, limit=24000)
        self.assertEqual(observed["contract_check"]["status"], "violation")
        with self.assertRaisesRegex(ValidationError, "Contract output guarantee"):
            self.app.delegation.review_agent(
                ident, fingerprint, "accepted", "The child result was reviewed"
            )
        rejected = self.app.delegation.review_agent(
            ident, fingerprint, "rejected", "Result omitted the required numeric field"
        )
        self.assertEqual(rejected["review"]["contract_check"]["status"], "violation")

    def test_rejected_required_child_does_not_satisfy_completion_gate(self):
        ident = self.spawn(context={"contract": {"outputs": {"result": "number"}}})
        fingerprint = self.collect(ident)
        self.app.delegation.review_agent(
            ident, fingerprint, "rejected", "The required numeric output was not produced"
        )
        completion = self.app.delegation.completion()
        self.assertFalse(completion["ready"])
        self.assertEqual(completion["unaccepted_workers"], [ident])

    def test_explicit_replacement_supersedes_rejected_obligation_without_erasing_audit(self):
        rejected = self.spawn(context={"contract": {"outputs": {"result": "number"}}})
        fingerprint = self.collect(rejected)
        self.app.delegation.review_agent(
            rejected,
            fingerprint,
            "rejected",
            "The child returned an object without the required numeric result.",
        )
        replacement = self.app.delegation.spawn_agent(
            "Return the finding using the revised interface",
            context={"contract": {"outputs": {"finding": "string"}}},
            replaces=rejected,
        )["agent_id"]
        retry = self.app.delegation.spawn_agent(
            "Return the finding using the revised interface",
            context={"contract": {"outputs": {"finding": "string"}}},
            replaces=rejected,
        )["agent_id"]
        self.assertEqual(retry, replacement)
        self.assertEqual(self.app.delegation.records[rejected]["superseded_by"], replacement)
        self.assertEqual(self.app.delegation.records[replacement]["replaces"], rejected)
        active = {row["id"] for row in self.app.delegation._current()}
        self.assertNotIn(rejected, active)
        pending = self.app.delegation.completion()
        self.assertEqual(pending["unaccepted_workers"], [])
        self.assertEqual(pending["unreviewed_workers"], [replacement])
        self.assertEqual(pending["superseded_workers"][0]["agent_id"], rejected)

        self.app.delegation.futures[replacement].result(timeout=5)
        replacement_fingerprint = self.collect(replacement)
        self.app.delegation.review_agent(
            replacement,
            replacement_fingerprint,
            "accepted",
            "The revised output shape was collected and checked independently.",
        )
        self.assertTrue(self.app.delegation.completion()["ready"])
        # The violating result remains directly readable for audit.
        self.assertEqual(self.app.delegation.records[rejected]["review"]["disposition"], "rejected")

    def test_replacement_requires_an_observed_rejected_or_blocked_review(self):
        original = self.spawn()
        with self.assertRaisesRegex(ValidationError, "blocked or rejected review"):
            self.app.delegation.spawn_agent("Replace too early", replaces=original)

    def test_replacement_preserves_requiredness_and_contract_obligation(self):
        original = self.spawn(context={"contract": {"outputs": {"result": "number"}}})
        fingerprint = self.collect(original)
        self.app.delegation.review_agent(
            original, fingerprint, "rejected", "The required numeric output was absent"
        )
        with self.assertRaisesRegex(ValidationError, "cannot weaken"):
            self.app.delegation.spawn_agent(
                "Optional retry", required=False, replaces=original
            )
        with self.assertRaisesRegex(ValidationError, "must declare a contract"):
            self.app.delegation.spawn_agent("Uncontracted retry", replaces=original)

    def test_superseded_children_are_historical_and_cannot_be_resumed_or_re_reviewed(self):
        original = self.spawn(context={"contract": {"outputs": {"result": "number"}}})
        fingerprint = self.collect(original)
        self.app.delegation.review_agent(
            original, fingerprint, "rejected", "The required numeric output was absent"
        )
        replacement = self.app.delegation.spawn_agent(
            "Return a numeric result", context={"contract": {"outputs": {"result": "number"}}}, replaces=original
        )["agent_id"]
        with self.assertRaisesRegex(ValidationError, "historical"):
            self.app.delegation.resume_agent(original)
        with self.assertRaisesRegex(ValidationError, "historical"):
            self.app.delegation.review_agent(
                original, fingerprint, "rejected", "re-review should be impossible"
            )
        self.assertNotEqual(original, replacement)

    def test_ordinary_handoff_signature_does_not_depend_on_null_replacement_lineage(self):
        plain = self.app.delegation._prepare_handoff(
            "Same semantic task", "Researcher", None, None, True
        )
        explicit_none = self.app.delegation._prepare_handoff(
            "Same semantic task", "Researcher", None, None, True, None
        )
        self.assertEqual(plain["signature"], explicit_none["signature"])

    def test_contract_type_labels_accept_bounded_human_readable_aliases(self):
        self.assertEqual(
            self.app.delegation._contract_observation(
                {"outputs": {"result": "JSON number, not a string"}},
                {"result": 42},
            )["status"],
            "pass",
        )

    def test_evidence_rechecks_only_witnesses_matched_to_contract_requirements(self):
        observation = self.app.delegation._evidence_observation(
            {
                "evidence_requirements": [
                    {"kind": "file_read", "path": "required.txt", "complete": True}
                ]
            },
            [
                {
                    "kind": "file_read",
                    "path": "required.txt",
                    "sha256": "a" * 64,
                    "complete": True,
                },
                {
                    "kind": "file_read",
                    "path": "incidental.txt",
                    "sha256": "b" * 64,
                    "complete": True,
                },
            ],
        )
        self.assertEqual(
            [item["path"] for item in observation["matched_witnesses"]], ["required.txt"]
        )

    def test_nested_delegation_contract_gates_child_cardinality(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "contract-coordinator",
            expected_children={"min_children": 1, "max_children": 1},
        )
        try:
            self.assertFalse(coord.completion()["delegation"]["ready"])
            ident = coord.spawn_agent("Complete the assigned nested branch")["agent_id"]
            coord.futures[ident].result(timeout=5)
            self.assertEqual(coord.completion()["delegation"]["actual"], 1)
            with self.assertRaisesRegex(ValidationError, "child quota"):
                coord.spawn_agent("An extra nested branch")
        finally:
            coord.close()

    def test_nested_delegation_cannot_count_optional_child(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "optional-contract-coordinator",
            expected_children={"min_children": 1, "max_children": 1},
        )
        try:
            with patch.object(coord.pool, "submit"):
                with self.assertRaisesRegex(ValidationError, "require every counted child"):
                    coord.spawn_agent("Optional branch", required=False)
        finally:
            coord.close()

    def test_batch_admission_rolls_back_when_registry_persist_fails(self):
        before = self.app.delegation.shared_budget["count"]
        with patch.object(self.app.delegation, "_save", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.app.delegation.spawn_agents(
                    [{"task": "first"}, {"task": "second"}]
                )
        self.assertEqual(self.app.delegation.shared_budget["count"], before)
        self.assertEqual(self.app.delegation.records, {})

    def test_batch_quota_failure_does_not_promote_existing_optional_child(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 1},
            provider=self.provider,
            root=self.root / "promotion-rollback",
        )
        try:
            existing = coord.spawn_agent("Existing optional branch", required=False)["agent_id"]
            prepared_existing = coord._prepare_handoff(
                "Existing optional branch", "Researcher", None, None, True
            )
            prepared_new = coord._prepare_handoff(
                "New branch", "Researcher", None, None, True
            )
            with self.assertRaisesRegex(ValidationError, "child quota"):
                coord._admit_prepared([prepared_existing, prepared_new])
            self.assertFalse(coord.records[existing]["required"])
        finally:
            coord.close()

    def test_invalid_nested_delegation_contract_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "child bounds"):
            self.app.delegation.spawn_agent(
                "nested",
                context={"contract": {"delegation": {"min_children": 2, "max_children": 1}}},
            )

    def test_nested_contract_prevents_accepting_worker_with_unfinished_child(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "nested-gate",
        )
        try:
            with patch.object(coord.pool, "submit"):
                ident = coord.spawn_agent(
                    "Delegate one nested branch",
                    context={"contract": {"delegation": {"min_children": 1, "max_children": 1}}},
                )["agent_id"]
            from flora.general.storage import atomic_json

            atomic_json(
                coord.root / ident / "result.json",
                {
                    "status": "completed",
                    "value": {"result": 1},
                    "reason": "",
                    "budget": {},
                    "nested_completion": {"ready": False},
                },
            )
            coord._update(ident, status="completed", detail="Finished")
            fingerprint = self._collect_from(coord, ident)
            with self.assertRaisesRegex(ValidationError, "Nested delegation contract"):
                coord.review_agent(ident, fingerprint, "accepted", "Reviewed")
        finally:
            coord.close()

    def test_close_persists_queued_children_as_paused(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "queued-close",
        )
        with patch.object(coord, "_submit"):
            ident = coord.spawn_agent("A child waiting for dispatch")["agent_id"]
        self.assertEqual(coord.records[ident]["status"], "queued")
        coord.close()
        self.assertEqual(coord.records[ident]["status"], "paused")
        self.assertEqual(coord.records[ident]["failure"]["code"], "paused")
        self.assertTrue(coord.completion()["waiting"] is False)
        self.assertIn(ident, coord.completion()["unreviewed_workers"])

    def test_zero_minimum_nested_contract_is_vacuously_reviewable_at_depth_limit(self):
        from flora.general.coordinator import Coordinator

        coord = Coordinator(
            self.app,
            {"enabled": True, "max_children": 2, "max_depth": 1},
            provider=self.provider,
            root=self.root / "zero-min-depth-limit",
            depth=1,
            expected_children={"min_children": 0, "max_children": 1},
        )
        try:
            ident = coord.spawn_agent(
                "A leaf may delegate zero optional children",
                context={"contract": {"delegation": {"min_children": 0, "max_children": 1}}},
            )["agent_id"]
            coord.futures[ident].result(timeout=5)
            fingerprint = self._collect_from(coord, ident)
            review = coord.review_agent(ident, fingerprint, "accepted", "Leaf result reviewed")
            self.assertEqual(review["disposition"], "accepted")
        finally:
            coord.close()

    @staticmethod
    def _collect_from(coord, ident):
        row = coord.read_agent(ident, limit=24000)
        while row.get("next_offset") is not None:
            row = coord.read_agent(ident, offset=row["next_offset"], limit=24000)
        return row["result_digest"]

    def test_invented_context_is_rejected_before_worker_dispatch(self):
        with self.assertRaisesRegex(ValidationError, "Unknown source"):
            self.app.delegation.spawn_agent("research", context={"source_ids": ["src-999999"]})
        self.assertEqual(self.provider.calls, 0)
        self.assertFalse(self.app.delegation.records)

    def test_invalid_contract_is_rejected_before_worker_dispatch(self):
        with self.assertRaisesRegex(ValidationError, "contract.guarantees"):
            self.app.delegation.spawn_agent(
                "research", context={"contract": {"guarantees": ["ok", 3]}}
            )
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

    def test_contracted_dependency_carries_producer_interface_and_review(self):
        contract = {"outputs": {"finding": "string"}, "guarantees": ["return finding"]}
        first = self.spawn("Independent contracted fact", context={"contract": contract})
        first_fingerprint = self.collect(first)
        self.app.delegation.review_agent(first, first_fingerprint, "accepted", "Checked output shape")
        second = self.spawn("Consume the reviewed dependency", depends_on=[first])
        dependency = self.app.delegation._result_view(second)["value"]["handoff"]["dependencies"][0]
        self.assertEqual(dependency["producer_contract"], contract)
        self.assertEqual(dependency["producer_review"]["disposition"], "accepted")

    def test_contracted_dependency_without_review_is_blocked(self):
        first = self.spawn(
            "Independent fact requiring review",
            context={"contract": {"outputs": {"finding": "string"}}},
        )
        second = self.spawn("Consume only reviewed dependency", depends_on=[first])
        self.assertEqual(self.app.delegation.records[second]["status"], "blocked")
        self.assertEqual(
            self.app.delegation.records[second]["failure"]["code"], "dependency_unreviewed"
        )

    def test_empty_explicit_contract_still_requires_dependency_review(self):
        first = self.spawn("Independent fact with explicit contract", context={"contract": {}})
        second = self.spawn("Consume only reviewed dependency", depends_on=[first])
        self.assertEqual(self.app.delegation.records[second]["status"], "blocked")
        self.assertEqual(
            self.app.delegation.records[second]["failure"]["code"], "dependency_unreviewed"
        )

    def test_dependency_rejects_producer_review_after_review_evidence_changes(self):
        import hashlib

        path = self.workspace / "dependency.txt"
        path.write_bytes(b"old")
        contract = {"outputs": {"finding": "string"}}
        first = self.spawn("Independent fact with mutable evidence", context={"contract": contract})
        fingerprint = self.collect(first)
        self.app.delegation.review_agent(
            first,
            fingerprint,
            "accepted",
            "Checked the producer output and source",
            evidence=[{"path": "dependency.txt", "sha256": hashlib.sha256(b"old").hexdigest()}],
        )
        path.write_bytes(b"new")
        second = self.spawn("Consume only current reviewed dependency", depends_on=[first])
        self.assertEqual(self.app.delegation.records[second]["status"], "blocked")
        self.assertEqual(
            self.app.delegation.records[second]["failure"]["code"], "dependency_unreviewed"
        )

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
