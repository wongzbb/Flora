"""Offline authored responses test probe accounting; they are not model evidence."""

import copy
import hashlib
import json
import re
import tempfile
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import patch

from flora.integrations.providers import ModelResponse, TransportError
from tests.helpers import block, bundle
from tests.live_mechanism_probe import (
    DispatchWorld,
    _redact,
    action_trajectory,
    assess,
    main,
    run_case,
    task_text,
)


def effect(tool, args, resume="done"):
    return {
        "op": "effect",
        "tool": tool,
        "args": args,
        "bind": "reply",
        "capture": {},
        "resume": resume,
    }


def done():
    return block(
        ["reply"],
        [{"op": "get", "dest": "value", "args": [{"var": "reply"}, "value"]}],
        {"op": "return", "value": {"var": "value"}},
    )


def commit_program(departure):
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(term=effect("commit_dispatch", {"departure_minutes": departure})),
            "done": done(),
        },
    }


def observation_program():
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(term=effect("read_cutoff", {}, "plan")),
            "plan": block(
                ["reply"],
                [
                    {"op": "get", "dest": "value", "args": [{"var": "reply"}, "value"]},
                    {"op": "get", "dest": "cutoff", "args": [{"var": "value"}, "cutoff_minutes"]},
                    {"op": "sub", "dest": "departure", "args": [{"var": "cutoff"}, 17]},
                ],
                effect("commit_dispatch", {"departure_minutes": {"var": "departure"}}),
            ),
            "done": done(),
        },
    }


def diagnostic_bundle(cutoffs, epoch, anchor):
    result = bundle(commit_program(cutoffs[0] - 17), epoch, anchor)
    result["programs"].append(
        {"id": "other", "program": commit_program(cutoffs[1] - 17), "inputs": {}}
    )
    result["diagnostics"] = [
        {
            "id": "verify_cutoff",
            "program": observation_program(),
            "inputs": {},
            "forecasts": [
                {
                    "candidate_id": name,
                    "predicate": {"op": "eq", "path": ["cutoff_minutes"], "value": cutoff},
                }
                for name, cutoff in zip(("main", "other"), cutoffs, strict=True)
            ],
            "witnesses": [{"cutoff_minutes": cutoff} for cutoff in cutoffs],
        }
    ]
    return result


class FixtureProvider:
    """Authored offline fixture; production probe never imports this class."""

    def __init__(self, cutoffs=(700, 940), ordinary=False, zero_score=False, wrong_consumer=False):
        self.cutoffs, self.ordinary, self.zero_score = cutoffs, ordinary, zero_score
        self.wrong_consumer = wrong_consumer
        self.views = []
        self.on_event = None

    def complete(self, messages, *, max_tokens):
        view = json.loads(messages[1]["content"])
        self.views.append(view)
        if self.ordinary:
            source = bundle(observation_program(), view["epoch"], view["trace_digest"])
        else:
            source = diagnostic_bundle(self.cutoffs, view["epoch"], view["trace_digest"])
            if self.zero_score:
                source["diagnostics"][0]["witnesses"] = []
            if self.wrong_consumer:
                # Still discriminates mechanically, but maps the actual state
                # to the opposite source's action. Structural score is not truth.
                source["diagnostics"][0]["program"]["blocks"]["plan"]["ops"][-1] = {
                    "op": "sub",
                    "dest": "departure",
                    "args": [sum(self.cutoffs) - 17, {"var": "cutoff"}],
                }
        return ModelResponse(json.dumps(source), 10, 20)


class MechanismProbeTests(unittest.TestCase):
    def execute(self, provider, state, elicitation="explicit"):
        with tempfile.TemporaryDirectory() as tmp:
            return run_case(
                provider,
                {"syntax": "block-list-v2", "prompt_style": "compact-v2"},
                (700, 940),
                state,
                Path(tmp),
                elicitation=elicitation,
            )

    def test_explicit_task_identity_is_unchanged_and_natural_only_removes_exercise(self):
        # Frozen from the pre-elicitation task, not recomputed from the new implementation.
        original_digest = "d2424c76f4b8b1b162e9fefb0633d00f5e6e93ae1e1a08f1e13b90fd76ff3968"
        explicit = task_text((700, 940))
        self.assertEqual(hashlib.sha256(explicit.encode()).hexdigest(), original_digest)
        self.assertEqual(explicit, task_text((700, 940), "explicit"))
        prefix, exercise = explicit.split("This is an explicitly elicited dual-control exercise: ")
        suffix = "All tools affect only this local simulation."
        self.assertTrue(exercise.endswith(suffix))
        natural = task_text((700, 940), "natural")
        self.assertEqual(natural, prefix + suffix)
        for term in ("dual-control", "candidate", "diagnostic", "forecast", "witness", "consumer"):
            self.assertNotIn(term, natural)
        self.assertIn("exactly 17 minutes before the CURRENT cutoff", natural)
        self.assertIn("Independently observe the current cutoff before committing", natural)
        self.assertIn("do not guess or commit both", natural)
        with self.assertRaises(ValueError):
            task_text((700, 940), "unknown")

    def test_both_actual_states_use_real_diagnostic_then_one_commit(self):
        tasks = []
        for state in (700, 940):
            with self.subTest(state=state):
                provider = FixtureProvider()
                row = self.execute(provider, state)
                self.assertTrue(row["grade"]["task_passed"], row)
                self.assertTrue(row["grade"]["mechanism_passed"], row)
                self.assertEqual(len(provider.views), 1)
                tasks.append(provider.views[0]["task"])
                self.assertEqual(provider.views[0]["receipts"], [])
                self.assertEqual(provider.views[0]["memory"], {})
                self.assertEqual(row["result"]["value"]["departure_minutes"], state - 17)
                self.assertEqual(row["mechanism_observations"]["inserted_diagnostic_actions"], 1)
                self.assertEqual(len(row["model_bundles"][0]["programs"]), 2)
                self.assertEqual(
                    [c["tool"] for c in row["host_calls"]], ["read_cutoff", "commit_dispatch"]
                )
                self.assertFalse(row["grade"]["spontaneous_synthesis_verified"])
        self.assertEqual(tasks[0], tasks[1])  # Hidden state has no task-side hint.

    def test_ordinary_observe_and_compute_is_task_success_not_mechanism_success(self):
        for elicitation in ("explicit", "natural"):
            with self.subTest(elicitation=elicitation):
                row = self.execute(FixtureProvider(ordinary=True), 940, elicitation)
                self.assertTrue(row["grade"]["task_passed"], row)
                self.assertFalse(row["grade"]["mechanism_passed"])
                self.assertEqual(row["mechanism_observations"]["inserted_diagnostic_actions"], 0)
                self.assertEqual(
                    [action["selection"]["diagnostic"] for action in row["action_trajectory"]],
                    [False, False],
                )

    def test_natural_task_keeps_compiler_exposure_and_no_causal_or_spontaneous_claim(self):
        tasks = []
        for state in (700, 940):
            provider = FixtureProvider()
            row = self.execute(provider, state, "natural")
            self.assertTrue(row["grade"]["task_passed"])
            self.assertTrue(row["grade"]["mechanism_passed"])
            for metadata in (row, row["grade"]):
                self.assertEqual(metadata["elicitation"], "natural")
                self.assertFalse(metadata["task_level_mechanism_elicited"])
                self.assertTrue(metadata["compiler_mechanism_instructions"])
                self.assertIn("compiler still teaches", metadata["scope"])
            self.assertFalse(row["grade"]["spontaneous_synthesis_verified"])
            self.assertFalse(row["grade"]["causal_benefit_verified"])
            self.assertEqual(provider.views[0]["receipts"], [])
            self.assertEqual(provider.views[0]["memory"], {})
            tasks.append(provider.views[0]["task"])
        self.assertEqual(tasks, [task_text((700, 940), "natural")] * 2)

    def test_trajectory_links_actual_receipts_without_executing_or_guessing_actions(self):
        row = self.execute(FixtureProvider(), 940, "natural")
        trajectory = row["action_trajectory"]
        self.assertEqual(len(trajectory), 2)
        for action, receipt in zip(trajectory, row["receipts"], strict=True):
            self.assertTrue(action["selection_linked"])
            self.assertEqual(action["event_id"], receipt["event_id"])
            self.assertEqual(action["previous_hash"], receipt["previous_hash"])
            self.assertEqual(action["request"], {key: receipt[key] for key in ("tool", "args")})
            self.assertEqual(action["status"], "returned")
            self.assertEqual(action["value"], receipt["value"])
        self.assertEqual(
            [action["selection"]["diagnostic"] for action in trajectory], [True, False]
        )
        self.assertEqual(
            {
                entry["request"]["args"]["departure_minutes"]
                for entry in trajectory[0]["selection"]["normal_frontiers"]
            },
            {683, 923},
        )
        self.assertEqual(
            [call["tool"] for call in row["host_calls"]], ["read_cutoff", "commit_dispatch"]
        )
        selections = copy.deepcopy(row["selections"])
        selections[0]["trace_digest"] = "not the observed anchor"
        unlinked = action_trajectory(row["receipts"], selections)
        self.assertFalse(unlinked[0]["selection_linked"])
        self.assertIsNone(unlinked[0]["selection"])
        self.assertTrue(unlinked[1]["selection_linked"])

    def test_positive_real_diagnostic_with_wrong_commit_is_not_a_pass(self):
        row = self.execute(FixtureProvider(wrong_consumer=True), 940)
        self.assertEqual(row["mechanism_observations"]["inserted_diagnostic_actions"], 1)
        self.assertTrue(row["grade"]["diagnostic_evidence_linked"])
        self.assertEqual(row["receipts"][0]["value"], {"cutoff_minutes": 940})
        self.assertEqual(row["receipts"][1]["args"], {"departure_minutes": 683})
        self.assertFalse(row["grade"]["task_passed"])
        self.assertFalse(row["grade"]["mechanism_passed"])

    def test_diagnostic_declaration_without_positive_insertion_is_not_a_pass(self):
        row = self.execute(FixtureProvider(zero_score=True), 700)
        self.assertFalse(row["grade"]["mechanism_passed"])
        self.assertEqual(row["mechanism_observations"]["inserted_diagnostic_actions"], 0)
        self.assertEqual(
            row["host_calls"], [{"tool": "commit_dispatch", "args": {"departure_minutes": 683}}]
        )

    def test_grading_requires_linked_forecasts_and_distinct_grounded_actions(self):
        row = self.execute(FixtureProvider(), 940)
        world = DispatchWorld(940)
        world.committed = 923
        world.calls = row["host_calls"]
        for elicitation, mutation in product(
            ("explicit", "natural"), ("feedback", "frontiers", "score")
        ):
            with self.subTest(mutation=mutation, elicitation=elicitation):
                events, selections = copy.deepcopy(row["events"]), copy.deepcopy(row["selections"])
                if mutation == "feedback":
                    for event in events:
                        if event.get("kind") == "forecast_observation":
                            event["report"]["event_id"] += 1
                elif mutation == "frontiers":
                    selections[0]["normal_frontiers"][1]["request"] = copy.deepcopy(
                        selections[0]["normal_frontiers"][0]["request"]
                    )
                else:
                    for event in events:
                        if event.get("kind") == "diagnostic_evaluated":
                            event["report"]["score"] = 0
                grade = assess(
                    world,
                    (700, 940),
                    row["result"],
                    row["receipts"],
                    events,
                    selections,
                    elicitation,
                )
                self.assertTrue(grade["task_passed"])
                self.assertFalse(grade["mechanism_passed"])

    def test_tool_does_not_repair_wrong_decisions_and_rejects_second_commit(self):
        world = DispatchWorld(940)
        self.assertEqual(world.commit_dispatch(10), {"committed": True, "departure_minutes": 10})
        self.assertEqual(world.committed, 10)
        with self.assertRaises(ValueError):
            world.commit_dispatch(923)
        self.assertEqual(world.committed, 10)

    def test_redaction_occurs_before_json_escaping(self):
        marker = 'local-test-"\\value'
        encoded = json.dumps(_redact({marker: [marker]}, marker))
        self.assertEqual(json.loads(encoded), {"[REDACTED]": ["[REDACTED]"]})

    def test_access_denial_stops_second_state_without_any_tool_call(self):
        for elicitation, status in product(("explicit", "natural"), (401, 402, 403, 404)):
            calls = []

            class Rejected:
                def __init__(self, **kwargs):
                    self.on_event = None

                def set_session_key(self, value):
                    pass

                def complete(self, messages, *, max_tokens):
                    calls.append(1)
                    raise TransportError(
                        f"model HTTP {status}: denied", category="http", status=status
                    )

            with (
                self.subTest(status=status, elicitation=elicitation),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                profile = root / "profile.json"
                profile.write_text(json.dumps({"provider": {}, "compiler": {}}))
                output = root / "run"
                argv = [
                    "probe",
                    "--model",
                    "deepseek-flash",
                    "--api-key-env",
                    "LOCAL_TEST_ONLY",
                    "--profile",
                    str(profile),
                    "--output",
                    str(output),
                    "--elicitation",
                    elicitation,
                ]
                with (
                    patch("tests.live_mechanism_probe.OpenAICompatibleProvider", Rejected),
                    patch(
                        "tests.live_mechanism_probe.os.environ", {"LOCAL_TEST_ONLY": "local-only"}
                    ),
                    patch("sys.argv", argv),
                    patch("builtins.print"),
                ):
                    self.assertEqual(main(), 3)
                self.assertEqual(calls, [1])
                rows = json.loads((output / "results.json").read_text())
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["access_rejected"], status)
                self.assertEqual(rows[0]["receipts"], [])
                self.assertEqual(rows[0]["action_trajectory"], [])
                self.assertEqual(rows[0]["grade"]["elicitation"], elicitation)
                self.assertEqual(rows[1]["status"], "not_run")
                self.assertEqual(rows[1]["elicitation"], elicitation)

    def test_cli_offline_runs_both_states_with_effective_options_and_no_seed_ir(self):
        self.assert_cli_offline()

    def test_cli_natural_threads_accurate_metadata_through_both_states(self):
        self.assert_cli_offline("natural")

    def assert_cli_offline(self, elicitation=None):
        instances, options = [], []

        class LocalProvider(FixtureProvider):
            def __init__(self, **kwargs):
                super().__init__()
                instances.append(self)
                options.append(kwargs)

            def set_session_key(self, value):
                pass

            def complete(self, messages, *, max_tokens):
                task = json.loads(messages[1]["content"])["task"]
                self.cutoffs = tuple(
                    int(re.search(f"record {label} says ([0-9]+)", task)[1]) for label in ("A", "B")
                )
                return super().complete(messages, max_tokens=max_tokens)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profile.json"
            profile.write_text(json.dumps({"provider": {"stream": False}, "compiler": {}}))
            output = root / "run"
            argv = [
                "probe",
                "--model",
                "deepseek-flash",
                "--api-key-env",
                "LOCAL_TEST_ONLY",
                "--profile",
                str(profile),
                "--output",
                str(output),
            ]
            if elicitation is not None:
                argv.extend(["--elicitation", elicitation])
            with (
                patch("tests.live_mechanism_probe.OpenAICompatibleProvider", LocalProvider),
                patch("tests.live_mechanism_probe.os.environ", {"LOCAL_TEST_ONLY": "local-only"}),
                patch("sys.argv", argv),
                patch("builtins.print"),
            ):
                self.assertEqual(main(), 0)
            rows = json.loads((output / "results.json").read_text())
            self.assertEqual(len(instances), 2)
            self.assertTrue(all(len(p.views) == 1 for p in instances))
            self.assertTrue(all(r["grade"]["mechanism_passed"] for r in rows))
            self.assertEqual(
                {r["actual_cutoff_after_run"] for r in rows}, set(rows[0]["source_cutoffs"])
            )
            self.assertEqual(rows[0]["task"], rows[1]["task"])
            self.assertTrue(all(o["stream"] and not o["allow_insecure_http"] for o in options))
            provenance = json.loads((output / "provenance.json").read_text())
            self.assertTrue(provenance["configuration"]["provider"]["stream"])
            self.assertEqual(provenance["configuration"]["compiler"]["syntax"], "block-list-v2")
            mode = elicitation or "explicit"
            for metadata in [provenance, *rows, *(row["grade"] for row in rows)]:
                self.assertEqual(metadata["elicitation"], mode)
                self.assertEqual(metadata["task_level_mechanism_elicited"], mode == "explicit")
                self.assertTrue(metadata["compiler_mechanism_instructions"])
            self.assertTrue(all(r["task"] == task_text(r["source_cutoffs"], mode) for r in rows))
            self.assertTrue(all(not r["grade"]["causal_benefit_verified"] for r in rows))

    def test_cli_rejects_other_endpoints_and_models_before_provider_construction(self):
        for change in (
            ["--base-url", "http://api.deepseek.com"],
            ["--base-url", "https://api.deepseek.com.example.invalid"],
            ["--model", "unapproved-model"],
            ["--elicitation", "unknown"],
        ):
            with self.subTest(change=change):
                argv = [
                    "probe",
                    "--model",
                    "deepseek-flash",
                    "--api-key-env",
                    "LOCAL_TEST_ONLY",
                    "--profile",
                    "/does-not-exist",
                    "--output",
                    "/does-not-exist",
                    *change,
                ]
                with (
                    patch("tests.live_mechanism_probe.OpenAICompatibleProvider") as provider,
                    patch("sys.argv", argv),
                    patch("sys.stderr"),
                    self.assertRaises(SystemExit),
                ):
                    main()
                provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
