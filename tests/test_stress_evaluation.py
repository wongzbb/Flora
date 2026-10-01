"""Offline oracle rejection, fixture variation and actual-journal evaluation tests."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from flora.general.agent import GeneralAgent
from flora.integrations.providers import ModelResponse
from flora.support.values import digest
from tests.helpers import bundle, pure
from tests.live_reliability_probe import (
    aggregate_usage,
    assess,
    collect_evidence,
    equal,
    evaluate,
    main,
    prepare_case,
    read_receipts,
    selected_cases,
    selected_models,
    unchanged_inputs,
    validate_options,
)
from tests.stress_cases import STRESS_TASKS


def receipt(tool="read_file", path="evidence.json", status="returned", **args):
    return {"tool": tool, "status": status, "args": {"path": path, **args}}


def worker(ident, path, value, *, depends_on=()):
    view = {
        "status": "completed",
        "value": value,
        "budget": {},
        "reason": None,
        "failure": None,
        "claims_verified": False,
    }
    length = len(json.dumps(view, ensure_ascii=False, separators=(",", ":")))
    return {
        "id": ident,
        "status": "completed",
        "_view": view,
        "_receipts": [receipt(path=path)],
        "depends_on": list(depends_on),
        "read_digest": digest(view),
        "read_windows": [[0, length]],
        "review": {"disposition": "accepted", "result_digest": digest(view)},
    }


class ReliabilityOracleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, value, receipts=()):
        return {"status": "completed", "value": value, "_receipts": list(receipts)}

    def grade(self, case, result, expected):
        return assess(case, result, self.root, [], expected)

    def test_guessed_correct_scalar_without_evidence_is_not_success(self):
        expected = prepare_case("read", self.root, 2)
        value = {"project": expected["project"], "total": expected["total"]}
        result = self.result(value)
        grade = self.grade("read", result, expected)
        self.assertTrue(grade["answer_correct"])
        self.assertFalse(grade["passed"])
        result["_receipts"] = [receipt()]
        self.assertTrue(self.grade("read", result, expected)["passed"])
        result["_receipts"][0]["status"] = "raised"
        self.assertFalse(self.grade("read", result, expected)["passed"])
        self.assertFalse(equal(True, 1))
        self.assertTrue(equal(123.0, 123))

    def test_search_provenance_uses_actual_match_paths_not_requested_directory(self):
        from tests.live_reliability_probe import observed_paths

        match = receipt("search_files", ".")
        match["value"] = {"matches": [{"path": "manual.txt", "text": "APPROVED fixture"}]}
        self.assertEqual(observed_paths([match]), {"manual.txt"})
        match["value"] = {"matches": []}
        self.assertEqual(observed_paths([match]), set())
        match["args"]["path"] = "manual.txt"
        self.assertEqual(observed_paths([match]), set())

    def test_report_requires_correct_content_source_and_current_bytes(self):
        expected = prepare_case("report", self.root, 4)
        source = {
            "source_id": "src-000001",
            "origin": "workspace:./evidence.json",
            "sha256": expected["input_hashes"]["evidence.json"],
        }
        result = self.result("./report.md", [receipt("write_report", "./report.md")])
        result["_sources"] = [source]

        def publish(content):
            (self.root / "report.md").write_text(content)
            result["artifacts"] = [
                {
                    "path": "report.md",
                    "current": True,
                    "current_task": True,
                    "kind": "report",
                    "sources": ["src-000001"],
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
            ]

        content = f"Project: {expected['project']}\nTotal: {expected['total']}\n[src-000001]\n"
        publish(content)
        self.assertTrue(self.grade("report", result, expected)["passed"])
        publish("empty but registered")
        self.assertFalse(self.grade("report", result, expected)["passed"])
        publish(content.replace(str(expected["total"]), str(expected["total"] + 1)))
        self.assertFalse(self.grade("report", result, expected)["passed"])
        publish(content)
        result["_sources"][0]["origin"] = "workspace:unrelated.json"
        self.assertFalse(self.grade("report", result, expected)["passed"])
        result["_sources"][0]["origin"] = "workspace:evidence.json"
        (self.root / "report.md").write_text(content + " changed")
        self.assertFalse(self.grade("report", result, expected)["passed"])

    def test_successful_artifact_with_invalid_final_program_still_fails(self):
        expected = prepare_case("write", self.root, 8)
        (self.root / "summary.json").write_text(
            json.dumps({"project": expected["project"], "total": expected["total"]})
        )
        result = self.result("summary.json", [receipt(), receipt("create_file", "summary.json")])
        self.assertTrue(self.grade("write", result, expected)["passed"])
        result["status"] = "needs_program"
        grade = self.grade("write", result, expected)
        self.assertTrue(grade["answer_correct"])
        self.assertFalse(grade["passed"])

    def test_branch_present_forbids_fallback_and_missing_requires_observed_error(self):
        expected = prepare_case("branch", self.root, 1)
        result = self.result(expected["branch_project"], [receipt(path="optional.json")])
        self.assertTrue(self.grade("branch", result, expected)["passed"])
        result["_receipts"].append(receipt())
        self.assertFalse(self.grade("branch", result, expected)["passed"])
        expected.update(optional_present=False, branch_project=expected["project"])
        result = self.result(expected["project"], [receipt()])
        self.assertFalse(self.grade("branch", result, expected)["passed"])
        result["_receipts"].insert(0, receipt(path="optional.json", status="raised"))
        self.assertTrue(self.grade("branch", result, expected)["passed"])

    def test_multiactor_guessed_total_or_same_worker_is_not_collaboration(self):
        expected = prepare_case("multi", self.root, 11)
        workers = [
            worker("left", "left.json", {"count": expected["counts"][0], "path": "left.json"}),
            worker("right", "right.json", {"count": expected["counts"][1], "path": "right.json"}),
        ]
        result = self.result(
            expected["total"], [receipt(path="left.json"), receipt(path="right.json")]
        )
        result["workers"] = workers
        self.assertTrue(self.grade("multi", result, expected)["passed"])
        for change in (
            "no_parent_read",
            "wrong_child",
            "same_identity",
            "incomplete_read",
            "stale_review",
        ):
            trial = copy.deepcopy(result)
            if change == "no_parent_read":
                trial["_receipts"] = []
            elif change == "wrong_child":
                trial["workers"][1] = copy.deepcopy(workers[0])
            elif change == "same_identity":
                trial["workers"][1]["id"] = "left"
            elif change == "incomplete_read":
                trial["workers"][0]["read_windows"] = [[0, 4]]
            else:
                trial["workers"][0]["review"]["result_digest"] = "old"
            with self.subTest(change=change):
                self.assertFalse(self.grade("multi", trial, expected)["passed"])

    def test_dependent_child_must_deliver_the_actual_dependency_values(self):
        expected = prepare_case("dependency", self.root, 12)
        left, right = expected["counts"]
        a = worker("a", "left.json", {"count": left, "path": "left.json"})
        b = worker(
            "b",
            "right.json",
            {"left": left, "right": right, "maximum": max(left, right)},
            depends_on=["a"],
        )
        result = self.result(
            max(left, right), [receipt(path="left.json"), receipt(path="right.json")]
        )
        result["workers"] = [a, b]
        self.assertTrue(self.grade("dependency", result, expected)["passed"])
        b["depends_on"] = []
        self.assertFalse(self.grade("dependency", result, expected)["passed"])
        b["depends_on"] = ["a"]
        b["_view"]["value"]["left"] += 1
        self.assertFalse(self.grade("dependency", result, expected)["passed"])

    def test_cost_sums_parent_and_children_once_and_preserves_unknown_usage(self):
        result = {
            "budget": {"model_calls": 2, "input_tokens": 100},
            "workers": [
                {
                    "budget": {"model_calls": 3, "input_tokens": 200, "unknown_usage_calls": 1},
                    "_view": {"budget": {"model_calls": 3}},
                }
            ],
        }
        self.assertEqual(
            aggregate_usage(result),
            {
                "model_calls": 5,
                "tool_calls": 0,
                "input_tokens": 300,
                "output_tokens": 0,
                "unknown_usage_calls": 1,
            },
        )

    def test_mutation_constraints_cover_every_input_and_unrequested_new_files(self):
        expected = prepare_case("read", self.root, 2)
        self.assertTrue(unchanged_inputs(self.root, expected))
        (self.root / "left.json").write_text("modified")
        self.assertFalse(unchanged_inputs(self.root, expected))
        expected = prepare_case("read", self.root, 2)
        (self.root / "forbidden.txt").write_text("injected")
        self.assertFalse(unchanged_inputs(self.root, expected))


class StressFixtureTests(unittest.TestCase):
    def test_every_family_varies_with_seed_is_reproducible_and_key_stays_outside(self):
        for case in STRESS_TASKS:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                roots = [Path(tmp) / str(n) for n in range(3)]
                for root in roots:
                    root.mkdir()
                a, b, c = [
                    prepare_case(case, root, seed)
                    for root, seed in zip(roots, (101, 101, 102), strict=True)
                ]
                self.assertEqual(a, b)
                self.assertNotEqual(a["answer"], c["answer"])
                self.assertFalse((roots[0] / "oracle.json").exists())
                self.assertTrue(unchanged_inputs(roots[0], a))

    def test_correct_stress_answers_require_evidence_and_wrong_answers_fail(self):
        for case in set(STRESS_TASKS) - {"edit_preserve", "dependency_route"}:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                expected = prepare_case(case, root, 201)
                result = {"status": "completed", "value": expected["answer"], "_receipts": []}
                self.assertFalse(assess(case, result, root, [], expected)["passed"])
                result["_receipts"] = [receipt(path=path) for path in expected["required_reads"]]
                self.assertTrue(assess(case, result, root, [], expected)["passed"])
                result["value"] = {"wrong": True}
                self.assertFalse(assess(case, result, root, [], expected)["passed"])

    def test_edit_requires_untouched_values_and_readback_after_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("edit_preserve", root, 17)
            (root / "settings.json").write_text(json.dumps(expected["files"]["settings.json"]))
            result = {
                "status": "completed",
                "value": expected["answer"],
                "_receipts": [
                    receipt(path="settings.json"),
                    receipt(path="request.json"),
                    receipt("update_file", "settings.json"),
                ],
            }
            self.assertFalse(assess("edit_preserve", result, root, [], expected)["passed"])
            result["_receipts"].append(receipt(path="settings.json"))
            self.assertTrue(assess("edit_preserve", result, root, [], expected)["passed"])
            modified = copy.deepcopy(expected["files"]["settings.json"])
            modified["unrelated"]["false"] = 0
            (root / "settings.json").write_text(json.dumps(modified))
            self.assertFalse(assess("edit_preserve", result, root, [], expected)["passed"])

    def test_route_disallows_unselected_reads_even_from_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("dependency_route", root, 55)
            a = worker(
                "a",
                "route.json",
                {"file": expected["selected"], "nonce": expected["answer"]["nonce"]},
            )
            b = worker("b", expected["selected"], expected["answer"], depends_on=["a"])
            result = {
                "status": "completed",
                "value": expected["answer"],
                "workers": [a, b],
                "_receipts": [receipt(path="route.json"), receipt(path=expected["selected"])],
            }
            self.assertTrue(assess("dependency_route", result, root, [], expected)["passed"])
            b["_receipts"].append(receipt(path=expected["forbidden_reads"][0]))
            self.assertFalse(assess("dependency_route", result, root, [], expected)["passed"])


class ProbeExecutionTests(unittest.TestCase):
    def options(self, output, **changes):
        return Namespace(
            output=output,
            models="deepseek-fixture,glm-fixture",
            cases="greet",
            rounds=1,
            profile=None,
            base_url="https://fixture.invalid/v1",
            seed=7,
            resume_attempts=0,
            allow_insecure_http=False,
            **changes,
        )

    def test_model_family_and_transport_guards_run_before_credentials(self):
        self.assertEqual(
            selected_models("deepseek-chat,glm5.3,THUDM/GLM-5"),
            ["deepseek-chat", "glm5.3", "THUDM/GLM-5"],
        )
        for names in ("gpt-4o", "claude/deepseek-test", "qwen-plus", "", "glm-5,glm-5"):
            with self.subTest(models=names), self.assertRaises(ValueError):
                selected_models(names)
        self.assertEqual(selected_cases("stress"), list(STRESS_TASKS))
        with tempfile.TemporaryDirectory() as tmp:
            args = self.options(Path(tmp) / "output")
            args.base_url = "http://fixture.invalid/v1"
            with self.assertRaises(ValueError):
                validate_options(args)
            args.allow_insecure_http = True
            validate_options(args)
        with (
            patch(
                "sys.argv",
                [
                    "probe",
                    "--base-url",
                    "https://fixture.invalid",
                    "--models",
                    "gpt-4o",
                    "--output",
                    "/tmp/test-forbidden-model",
                ],
            ),
            patch("getpass.getpass") as secret,
            patch("argparse.ArgumentParser.error", side_effect=ValueError),
        ):
            with self.assertRaises(ValueError):
                main()
            secret.assert_not_called()

    def test_real_local_journal_preflight_and_read_only_collection(self):
        class Provider:
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                return ModelResponse(
                    json.dumps(bundle(pure("hello"), view["epoch"], view["trace_digest"])), 3, 2
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            with GeneralAgent(
                session_dir=Path(tmp) / "session", workspace=root, provider=Provider()
            ) as app:
                result = app.run("hello")
                before = app.agent.history
                collect_evidence(app, result)
                self.assertEqual(app.agent.history, before)
                self.assertEqual(read_receipts(app.agent.current_trace_path), [])
                self.assertTrue(assess("greet", result, root, [], {})["passed"])

    def test_whole_runner_with_real_agent_and_scripted_provider_is_offline(self):
        class Provider:
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                return ModelResponse(
                    json.dumps(bundle(pure("hello"), view["epoch"], view["trace_digest"])), 3, 2
                )

        def construct(**kwargs):
            kwargs["provider"] = Provider()
            kwargs["session_key"] = None
            kwargs["profile"].pop("provider", None)
            return GeneralAgent(**kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
            patch("builtins.print"),
        ):
            output = Path(tmp) / "evaluation"
            rows = evaluate(self.options(output), "offline-fixture-key")
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["grade"]["passed"] for row in rows))
            self.assertTrue(all(row["aggregate_usage"]["model_calls"] == 1 for row in rows))
            self.assertEqual(rows[0]["fixture_seed"], rows[1]["fixture_seed"])
            self.assertEqual(len(list(output.glob("case-*/workspace/evidence.json"))), 2)
            manifest = (output / "evaluation.json").read_text()
            self.assertNotIn("offline-fixture-key", manifest)

    def test_actual_worker_journals_are_collected_without_marking_results_read(self):
        from tests.test_reliability_integration import MultiProvider

        class PathProvider(MultiProvider):
            def complete(self, messages, *, max_tokens):
                response = super().complete(messages, max_tokens=max_tokens)
                context = json.loads(messages[1]["content"])
                data = json.loads(response.text)
                if "spawn_agent" not in {t["name"] for t in context["tools"]}:
                    path = "left.json" if "left.json" in context["task"] else "right.json"
                    done = data["programs"][0]["program"]["blocks"]["done"]
                    done["ops"].append(
                        {"op": "get", "dest": "count", "args": [{"var": "parsed"}, "count"]}
                    )
                    data["programs"][0]["program"]["blocks"]["main"]["term"]["args"]["path"] = (
                        "./" + path
                    )
                    done["term"]["value"] = {"count": {"var": "count"}, "path": "./" + path}
                return ModelResponse(json.dumps(data), 12, 8)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (root / "left.json").write_text('{"count":17}')
            (root / "right.json").write_text('{"count":24}')
            with GeneralAgent(
                session_dir=Path(tmp) / "session",
                workspace=root,
                provider=PathProvider(),
                profile={"general": {"subagents": {"enabled": True}}},
            ) as app:
                result = app.run("Use two independent readers and verify their claims")
                before = copy.deepcopy(app.delegation.records)
                collect_evidence(app, result)
                self.assertEqual(app.delegation.records, before)
                self.assertEqual(len(result["workers"]), 2)
                self.assertTrue(all(len(w["_receipts"]) == 1 for w in result["workers"]))
                expected = {"counts": [17, 24], "total": 41, "project": "test"}
                self.assertTrue(assess("multi", result, root, [], expected)["passed"])
                self.assertEqual(aggregate_usage(result)["model_calls"], 3)

    def test_transport_failure_stops_later_paid_trials_without_erasing_them(self):
        from flora.integrations.providers import TransportError

        calls = []

        class Provider:
            def complete(self, messages, *, max_tokens):
                calls.append(1)
                raise TransportError("Fixture connection refused", category="connection")

        def construct(**kwargs):
            kwargs["provider"] = Provider()
            kwargs["session_key"] = None
            kwargs["profile"].pop("provider", None)
            return GeneralAgent(**kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
            patch("builtins.print"),
        ):
            args = self.options(Path(tmp) / "evaluation")
            args.cases, args.rounds, args.resume_attempts = "greet,compute", 2, 1
            rows = evaluate(args, "offline-fixture-key")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(rows), 8)
            self.assertEqual(sum(r["status"] == "not_run" for r in rows), 6)
            self.assertEqual(sum(r["grade"]["passed"] is True for r in rows), 0)
            manifest = json.loads((args.output / "evaluation.json").read_text())
            self.assertEqual(len(manifest["results"]), 8)
            self.assertEqual(sum(s["attempted"] for s in manifest["summary"]), 2)
            self.assertEqual(sum(s["unrun"] for s in manifest["summary"]), 6)

    def test_explicit_environment_key_is_memory_only_and_does_not_prompt(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict("os.environ", {"FLORA_EVAL_TEST_KEY": "fixture-only-secret"}),
            patch("getpass.getpass") as prompt,
            patch(
                "tests.live_reliability_probe.evaluate",
                return_value=[{"status": "completed", "grade": {"passed": True}}],
            ) as run,
            patch(
                "sys.argv",
                [
                    "probe",
                    "--base-url",
                    "https://fixture.invalid/v1",
                    "--models",
                    "glm5.3",
                    "--output",
                    str(Path(tmp) / "evaluation"),
                    "--api-key-env",
                    "FLORA_EVAL_TEST_KEY",
                ],
            ),
        ):
            self.assertEqual(main(), 0)
            prompt.assert_not_called()
            self.assertEqual(run.call_args.args[1], "fixture-only-secret")
            self.assertEqual(run.call_args.args[0].api_key_env, "FLORA_EVAL_TEST_KEY")

    def test_mechanism_counts_are_observations_not_semantic_success(self):
        from tests.live_reliability_probe import mechanism_observations

        stats = mechanism_observations(
            [
                {"kind": "bundle_installed", "normal_candidates": ["a", "b"]},
                {"kind": "subagent_event", "event": {"kind": "consumer_check"}},
                {"kind": "diagnostic_evaluated"},
                {"kind": "revision_checked"},
            ]
        )
        self.assertEqual(stats["multiple_candidate_bundles"], 1)
        self.assertEqual(stats["consumer_check"], 1)
        self.assertEqual(stats["diagnostic_evaluated"], 1)
        self.assertFalse(stats["semantic_or_causal_validation"])


class EvaluationBlockerRegressions(unittest.TestCase):
    def test_access_rejection_stops_all_models_even_with_transport_override(self):
        from flora.integrations.providers import TransportError

        for status in (401, 402, 403, 404):
            calls = []

            class Rejected:
                def complete(self, messages, *, max_tokens):
                    calls.append(1)
                    raise TransportError(
                        f"model HTTP {status}: rejected", category="http", status=status
                    )

            def construct(**kwargs):
                kwargs["provider"] = Rejected()
                kwargs["session_key"] = None
                kwargs["profile"].pop("provider", None)
                return GeneralAgent(**kwargs)

            with (
                self.subTest(status=status),
                tempfile.TemporaryDirectory() as tmp,
                patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
                patch("builtins.print"),
            ):
                args = self.options(Path(tmp) / "evaluation")
                args.cases, args.rounds = "greet,compute", 2
                args.continue_on_transport_error = True
                rows = evaluate(args, "fixture-only")
                self.assertEqual(len(calls), 1)
                self.assertEqual(sum(r["status"] == "not_run" for r in rows), len(rows) - 1)
                failure = next(r for r in rows if r["status"] != "not_run")["failure"]
                self.assertEqual(failure["http_status"], status)

    def test_explicit_profile_request_deadlines_are_preserved(self):
        profiles = []

        def construct(**kwargs):
            profiles.append(copy.deepcopy(kwargs["profile"]))
            return self.construct(**kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
            patch("builtins.print"),
        ):
            args = self.options(Path(tmp) / "evaluation")
            args.profile = Path(tmp) / "profile.json"
            args.profile.write_text(
                json.dumps(
                    {
                        "provider": {"timeout": 90, "total_timeout": 360},
                        "compiler": {"compilation_timeout": 360},
                    }
                )
            )
            rows = evaluate(args, "fixture-only")
            self.assertTrue(all(r["grade"]["passed"] for r in rows))
            self.assertTrue(profiles)
            for profile in profiles:
                self.assertEqual(profile["provider"]["timeout"], 90)
                self.assertEqual(profile["provider"]["total_timeout"], 360)
                self.assertEqual(profile["compiler"]["compilation_timeout"], 360)

    options = ProbeExecutionTests.options

    @staticmethod
    def provider(*, transport=False):
        class Provider:
            def complete(self, messages, *, max_tokens):
                if transport:
                    from flora.integrations.providers import TransportError

                    raise TransportError("Fixture connection refused", category="connection")
                view = json.loads(messages[1]["content"])
                return ModelResponse(
                    json.dumps(bundle(pure("hello"), view["epoch"], view["trace_digest"])), 3, 2
                )

        return Provider()

    def construct(self, **kwargs):
        kwargs["provider"] = self.provider()
        kwargs["session_key"] = None
        kwargs["profile"].pop("provider", None)
        return GeneralAgent(**kwargs)

    def test_empty_and_partial_limits_are_filled_and_resolved_limits_recorded(self):
        from tests.live_reliability_probe import (
            CHILD_LIMITS,
            PARENT_LIMITS,
            apply_evaluation_limits,
        )

        for profile in (
            {"budget": {}, "general": {"subagents": {"budget": {}}}},
            {
                "budget": {"max_model_calls": None, "max_wall_seconds": 7},
                "general": {
                    "subagents": {"budget": {"max_tool_calls": 3, "max_output_tokens": None}}
                },
            },
        ):
            original = copy.deepcopy(profile)
            expected = apply_evaluation_limits(copy.deepcopy(profile))
            self.assertEqual(expected["parent"], {**PARENT_LIMITS, **original["budget"]})
            self.assertEqual(
                expected["per_child"],
                {**CHILD_LIMITS, **original["general"]["subagents"]["budget"]},
            )
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch("tests.live_reliability_probe.GeneralAgent", side_effect=self.construct),
                patch(
                    "tests.live_reliability_probe.read_profile",
                    side_effect=lambda _: copy.deepcopy(original),
                ),
                patch("builtins.print"),
            ):
                args = self.options(Path(tmp) / "evaluation")
                args.models, args.profile = "glm-fixture", Path("unused-fixture-profile")
                rows = evaluate(args, "fixture-only")
                self.assertTrue(rows[0]["grade"]["passed"])
                self.assertEqual(rows[0]["resolved_limits"], expected)
                manifest = json.loads((args.output / "evaluation.json").read_text())
                self.assertEqual(manifest["results"][0]["resolved_limits"], expected)

    def test_actual_path_alias_reads_writes_and_readback_are_equivalent(self):
        from flora.general.documents import DocumentWorkspace
        from tests.live_reliability_probe import workspace_path

        for invalid in (
            "../evidence.json",
            "/evidence.json",
            "a/../evidence.json",
            "C:evidence.json",
            "a\\evidence.json",
        ):
            self.assertIsNone(workspace_path(invalid))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("read", root, 19)
            files = DocumentWorkspace(root)
            read = receipt(path="./evidence.json")
            read["value"] = files.read_file("./evidence.json")
            self.assertEqual(read["value"]["path"], "evidence.json")
            result = {
                "status": "completed",
                "value": {"project": expected["project"], "total": expected["total"]},
                "_receipts": [read],
            }
            self.assertTrue(assess("read", result, root, [], expected)["passed"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("edit_preserve", root, 19)
            files = DocumentWorkspace(root)
            reads = [receipt(path="./settings.json"), receipt(path="./request.json")]
            for read in reads:
                read["value"] = files.read_file(read["args"]["path"])
            write = receipt("write_file", "./settings.json")
            write["value"] = files.write_file(
                "./settings.json",
                json.dumps(expected["files"]["settings.json"]),
                expected_sha256=reads[0]["value"]["sha256"],
            )
            readback = receipt(path="./settings.json")
            readback["value"] = files.read_file("./settings.json")
            result = {
                "status": "completed",
                "value": {**expected["answer"], "path": "./settings.json"},
                "_receipts": [*reads, write, readback],
            }
            self.assertTrue(assess("edit_preserve", result, root, [], expected)["passed"])

    def test_new_empty_directory_violates_read_only_case(self):
        from flora.general.documents import DocumentWorkspace
        from tests.live_reliability_probe import snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("branch", root, 1)
            files = DocumentWorkspace(root)
            read = receipt(path="./optional.json")
            read["value"] = files.read_file("./optional.json")
            result = {
                "status": "completed",
                "value": expected["branch_project"],
                "_receipts": [read],
            }
            self.assertTrue(assess("branch", result, root, [], expected)["passed"])
            mkdir = receipt("make_directory", "forbidden")
            mkdir["value"] = files.make_directory("forbidden")
            result["_receipts"].append(mkdir)
            self.assertEqual(snapshot(root)["forbidden/"], "DIRECTORY")
            self.assertFalse(unchanged_inputs(root, expected))
            self.assertFalse(assess("branch", result, root, [], expected)["passed"])

    def test_forbidden_alias_and_no_match_search_cannot_hide_reads(self):
        from flora.general.documents import DocumentWorkspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = prepare_case("dependency_route", root, 55)
            files = DocumentWorkspace(root)
            a = worker(
                "a",
                "./route.json",
                {"file": "./" + expected["selected"], "nonce": expected["answer"]["nonce"]},
            )
            b = worker("b", "./" + expected["selected"], expected["answer"], depends_on=["a"])
            result = {
                "status": "completed",
                "value": expected["answer"],
                "workers": [a, b],
                "_receipts": [
                    receipt(path="./route.json"),
                    receipt(path="./" + expected["selected"]),
                ],
            }
            self.assertTrue(assess("dependency_route", result, root, [], expected)["passed"])
            forbidden = receipt(path="./" + expected["forbidden_reads"][0])
            forbidden["value"] = files.read_file(forbidden["args"]["path"])
            b["_receipts"].append(forbidden)
            self.assertFalse(assess("dependency_route", result, root, [], expected)["passed"])
            b["_receipts"].pop()
            search = receipt("search_files", ".", query="NEVER_OCCURS")
            search["value"] = files.search_files("NEVER_OCCURS")
            self.assertEqual(search["value"]["matches"], [])
            b["_receipts"].append(search)
            self.assertFalse(assess("dependency_route", result, root, [], expected)["passed"])
            search["args"]["glob"] = expected["selected"]
            search["value"] = files.search_files("NEVER_OCCURS", glob=expected["selected"])
            self.assertTrue(assess("dependency_route", result, root, [], expected)["passed"])

    def test_legacy_transport_reason_trips_breaker_without_persisting_reason(self):
        calls = []

        def construct(**kwargs):
            calls.append(1)
            kwargs["provider"] = self.provider(transport=True)
            kwargs["session_key"] = None
            kwargs["profile"].pop("provider", None)
            kwargs["profile"]["general"]["protocol"] = "general-v1"
            return GeneralAgent(**kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
            patch("builtins.print"),
        ):
            args = self.options(Path(tmp) / "evaluation")
            args.models, args.cases, args.rounds = "deepseek-fixture", "greet,compute", 2
            rows = evaluate(args, "fixture-only")
            self.assertEqual(len(calls), 1)
            self.assertEqual(sum(r["status"] == "not_run" for r in rows), 3)
            attempted = next(r for r in rows if r["status"] != "not_run")
            self.assertEqual(attempted["failure"]["code"], "model_transport")
            self.assertNotIn(
                "Fixture connection refused", (args.output / "evaluation.json").read_text()
            )

    def test_collection_failure_retains_observed_runtime_completion(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=self.construct),
            patch("tests.live_reliability_probe.collect_evidence", side_effect=ValueError),
            patch("builtins.print"),
        ):
            rows = evaluate(self.options(Path(tmp) / "evaluation"), "fixture-only")
            for row in rows:
                self.assertEqual(row["status"], "probe_error")
                self.assertEqual(row["runtime_status"], "completed")
                self.assertEqual(row["failure_phase"], "collect_evidence")
                self.assertTrue(row["grade"]["runtime_completed"])
                self.assertFalse(row["grade"]["passed"])

    def test_incomplete_research_is_failure_and_main_never_signals_success(self):
        from tests.live_reliability_probe import exit_status

        def construct(**kwargs):
            kwargs["provider"] = self.provider(transport=True)
            kwargs["session_key"] = None
            kwargs["profile"].pop("provider", None)
            return GeneralAgent(**kwargs)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tests.live_reliability_probe.GeneralAgent", side_effect=construct),
            patch("getpass.getpass", return_value="fixture-only"),
            patch("builtins.print"),
            patch(
                "sys.argv",
                [
                    "probe",
                    "--base-url",
                    "https://fixture.invalid/v1",
                    "--models",
                    "glm-fixture",
                    "--output",
                    str(Path(tmp) / "evaluation"),
                    "--cases",
                    "research",
                    "--rounds",
                    "2",
                ],
            ),
        ):
            self.assertEqual(main(), 1)
            manifest = json.loads((Path(tmp) / "evaluation" / "evaluation.json").read_text())
            self.assertEqual(manifest["summary"][0]["graded"], 1)
            self.assertEqual(manifest["summary"][0]["review_required"], 0)
            self.assertEqual(manifest["summary"][0]["unrun"], 1)
        self.assertEqual(exit_status([]), 1)
        self.assertEqual(
            exit_status(
                [{"status": "completed", "grade": {"passed": None, "review_required": True}}]
            ),
            2,
        )
