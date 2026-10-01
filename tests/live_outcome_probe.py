"""Opt-in small outcome probe, reusing the reliability probe's execution lifecycle.

One model, arm and layout; one attempt per explicitly selected family (at most 3).
This module neither requests credentials nor calls a model merely by importing it.
Requires the complete_versions-enabled reliability probe from the same release.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

from tests.outcome_probe_cases import EXPLICIT_CHECK, FAMILIES, assess_outcome, make_outcome_fixture

MODELS = ("deepseek-flash", "deepseek-v4-pro")
BASE_URL = "https://api.deepseek.com:443"


def load_support(path=None):
    """Load a private module instance; never patch an imported shared probe module."""
    path = Path(path) if path else Path(__file__).with_name("live_reliability_probe.py")
    spec = importlib.util.spec_from_file_location("tests._outcome_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "complete_versions", None)):
        raise ValueError("Outcome probe requires the complete_versions-enabled reliability probe")
    return module


def source_entries(result, complete_versions):
    """Union actual parent/child observations without imposing a delegation route."""
    receipts = list(result.get("_receipts", []))
    for worker in result.get("workers", []):
        receipts.extend(worker.get("_receipts", []))
    versions = complete_versions(receipts)
    return [
        {"path": path, "sha256": sha, "complete": True}
        for path, hashes in sorted(versions.items())
        for sha in sorted(hashes)
    ]


def validate_bounded_profile(support, profile_path):
    profile = support.read_profile(profile_path)
    limits = support.apply_evaluation_limits(profile)
    for ledger in limits.values():
        for name, value in ledger.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Outcome probe requires finite positive {name}")
    return limits


def configure_support(support, options):
    """Adapt only fixture/oracle selection; retain provider setup, journals and safety."""
    original_validate = support.validate_options
    original_provenance = support.source_provenance
    original_monitor = support.AccessFailureMonitor
    run_evaluation = support.evaluate
    monitors = []
    task_contexts = {}
    support.TASKS = {family: "Fixture task is assigned before execution" for family in FAMILIES}
    support.COLLABORATIVE = set(FAMILIES)  # Available to every arm, never required by its task.

    def selected_cases(text):
        cases = text.split(",")
        if (
            not cases
            or len(cases) > 3
            or len(set(cases)) != len(cases)
            or any(c not in FAMILIES for c in cases)
        ):
            raise ValueError("Choose one to three distinct outcome families")
        return cases

    def validate(args):
        models, cases = original_validate(args)
        if len(models) != 1 or models[0] not in MODELS or args.base_url != BASE_URL:
            raise ValueError(
                "Use one authorized DeepSeek model and the fixed official HTTPS endpoint"
            )
        if (
            args.rounds != 1
            or getattr(args, "resume_attempts", 0) != 0
            or getattr(args, "reopen_after_write", False)
        ):
            raise ValueError("Outcome probe runs one attempt per family, without automatic resume")
        repository = Path(__file__).resolve().parents[1]
        output = args.output.resolve()
        if output == repository or repository in output.parents:
            raise ValueError("Evaluation output must be outside the repository")
        validate_bounded_profile(support, args.profile)
        return models, cases

    def prepare(family, root, seed):
        fixture = make_outcome_fixture(
            family, root, seed, layout=options.layout, explicit=options.arm == "explicit"
        )
        support.TASKS[family] = fixture["task"]
        natural_task = fixture["task"].removesuffix(" " + EXPLICIT_CHECK)
        task_contexts[family] = {
            "natural_task_sha256": hashlib.sha256(natural_task.encode()).hexdigest(),
            "explicit_task_sha256": hashlib.sha256(
                (natural_task + " " + EXPLICIT_CHECK).encode()
            ).hexdigest(),
            "task_sha256": hashlib.sha256(fixture["task"].encode()).hexdigest(),
            "effective_layout": "embedded-only" if family == "embedded_task" else options.layout,
            "source_schema_shift_applicable": family != "embedded_task",
            "fixture_seed": seed,
            "arm": options.arm,
            "layout": options.layout,
        }
        oracle = fixture["oracle"]
        # Reuse the stronger directory/symlink-aware mutation check as well.
        oracle["input_hashes"] = support.snapshot(root)
        return oracle

    def assess(family, result, root, events, oracle):
        grade = assess_outcome(
            result.get("status"),
            result.get("value"),
            root,
            oracle,
            observed_sources=source_entries(result, support.complete_versions),
        )
        grade.update(
            runtime_completed=result.get("status") == "completed",
            review_required=False,
            evidence_integrity=grade["evidence_complete"] and grade["workspace_preserved"],
        )
        grade["evidence_scope"] = (
            "Complete actual source versions across parent and workers; not proof of semantic use"
        )
        return grade

    def provenance():
        result = original_provenance()
        result["outcome_driver"] = {
            "arm": options.arm,
            "layout": options.layout,
            "max_case_runs": len(options.families),
            "planned_families": list(options.families),
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "fixtures_sha256": hashlib.sha256(
                Path(__file__).with_name("outcome_probe_cases.py").read_bytes()
            ).hexdigest(),
            "support_sha256": hashlib.sha256(Path(support.__file__).read_bytes()).hexdigest(),
            "task_contexts": copy.deepcopy(task_contexts),
        }
        return result

    class Monitor(original_monitor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            monitors.append(self)
            self.denial = None

        def __call__(self, event):
            super().__call__(event)
            failure = event.get("event", {}) if event.get("kind") == "subagent_event" else event
            if failure.get("kind") == "transcript" and failure.get("channel") == "model_failure":
                try:
                    failure = json.loads(failure["text"])
                except (ValueError, KeyError):
                    return
            if not isinstance(failure, dict) or failure.get("kind") != "model_failure":
                return
            status = failure.get("http_status")
            if status in (401, 402, 403, 404):
                self.denial = {"kind": "access_rejected", "http_status": status}
                self.status = status
            elif re.search(
                r"insufficient[_ ](?:balance|quota)|balance.{0,32}(?:insufficient|exhausted)|billing_hard_limit_reached|余额不足",
                " ".join(str(failure.get(k, "")) for k in ("code", "category", "message")),
                re.I,
            ):
                # Preserve the actual event; never invent an HTTP code for semantic denial.
                self.denial = {
                    "kind": "balance_rejected",
                    "http_status": status if type(status) is int else None,
                }
            if self.denial and self.app:
                self.app.request_pause()

    support.selected_cases = selected_cases
    support.validate_options = validate
    support.prepare_case = prepare
    support.assess = assess
    support.source_provenance = provenance
    support.AccessFailureMonitor = Monitor

    def evaluate(args, key):
        validate(args)
        output = args.output.resolve()
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
        rows, stopped = [], None
        for family in options.families:
            if stopped:
                rows.append(
                    {
                        "case": family,
                        "model": options.model,
                        "arm": options.arm,
                        "layout": options.layout,
                        "status": "not_run",
                        "stop": stopped,
                        "grade": {
                            "passed": False,
                            "runtime_completed": False,
                            "review_required": False,
                        },
                    }
                )
            else:
                case_args = copy.copy(args)
                case_args.cases, case_args.output = family, output / family
                begin = len(monitors)
                batch = run_evaluation(case_args, key)
                for row in batch:
                    row.update(
                        task_contexts.get(family, {"arm": options.arm, "layout": options.layout})
                    )
                    denial = next((m.denial for m in monitors[begin:] if m.denial), None)
                    if denial:
                        stopped = denial
                        row["stop"] = denial
                        row["grade"]["passed"] = False
                    elif (
                        row.get("status") == "probe_error"
                        or (row.get("failure") or {}).get("code") == "model_transport"
                    ):
                        stopped = {"kind": "operational_failure"}
                    rows.append(row)
            support.atomic_json(
                output / "outcome-evaluation.json",
                {
                    "results": rows,
                    "arm": options.arm,
                    "layout": options.layout,
                    "source": provenance(),
                    "model": options.model,
                    "base_url": BASE_URL,
                    "scope": "Outcome and actual observation coverage; local check execution needs separate trace review",
                },
            )
        return rows

    support.evaluate = evaluate
    return support


def main(argv=None, *, support=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=MODELS)
    parser.add_argument(
        "--family", dest="families", action="append", required=True, choices=FAMILIES
    )
    parser.add_argument("--arm", choices=("natural", "explicit"), default="natural")
    parser.add_argument("--layout", choices=("json-v1", "csv-v2"), default="json-v1")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--seed", type=int, default=20261001)
    options = parser.parse_args(argv)
    if len(options.families) > 3 or len(set(options.families)) != len(options.families):
        parser.error("Choose at most three distinct families")
    try:
        support = configure_support(support or load_support(), options)
    except ValueError as exc:
        parser.error(str(exc))
    translated = [
        "outcome-probe",
        "--models",
        options.model,
        "--base-url",
        BASE_URL,
        "--cases",
        ",".join(options.families),
        "--rounds",
        "1",
        "--seed",
        str(options.seed),
        "--profile",
        str(options.profile),
        "--output",
        str(options.output),
        "--api-key-env",
        options.api_key_env,
    ]
    # Reuse the established credential entrypoint; no extra key reads, copies,
    # persistence, debugging or preflight. CLI invocation is single-threaded.
    previous = sys.argv
    try:
        sys.argv = translated
        return support.main()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    raise SystemExit(main())
