"""Opt-in multi-model, repeated real-API task evaluation. Credentials stay in memory.

Run with PYTHONPATH=src python tests/live_reliability_probe.py --base-url URL
--models ID,ID --output /outside/repository. Tests never run this automatically.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from flora.general.agent import GeneralAgent, read_profile
from flora.general.reliability import failure_info
from flora.general.storage import atomic_json

TASKS = {
    "greet": "hello",
    "compute": "Compute (17 + 24) * 3. Return only the number.",
    "read": "Read evidence.json and return the project field and sum of counts as an object with project and total.",
    "write": "Read evidence.json and create summary.json with project and the sum of counts under total. Do not modify evidence.json. Return the file path.",
    "branch": "Read optional.json if present and return its project. Only if missing, read evidence.json and return its project. Do not create anything.",
    "table": "Use sales.csv to compute the sum of the amount column. Return only the exact total.",
    "multi": "Use two independent read-only subagents: one reads left.json, the other right.json. Have them return the observed count field and file path. Collect their complete actual results, inspect the two original files, review both workers, and return the sum as a number. Do not guess or claim success before checking both results.",
    "dependency": "Delegate to a child that reads left.json and reports its observed count and file path. Delegate to another child with depends_on the first; ask it to compare that actual dependency answer to right.json. Collect and review both actual answers, inspect source files yourself, and return the larger count as a number.",
    "report": "Read evidence.json and produce report.md using write_report. Include the project and exact total and an observed source reference. Return the real publication path.",
    "research": "Research the most recent available official documentation on agent evaluation. Use web search and read the actual official pages, use read-only workers when useful, return evidence-linked findings and unresolved limitations. Never present blocked access as successful research.",
}


def assess(case, result, root, events, expected):
    value = result.get("value")
    calls = [e.get("tool") for e in events if e.get("channel") == "tool/call"]
    ready = result.get("status") == "completed"
    checks = {
        "greet": isinstance(value, str)
        and bool(re.search(r"hello|hi\b|你好", value, re.I))
        and not calls,
        "compute": value == 123 or value == "123",
        "read": value == {"project": expected["project"], "total": expected["total"]},
        "branch": value == expected["project"],
        "table": value == expected["total"] or value == str(expected["total"]),
        "multi": value == expected["total"],
        "dependency": value == max(expected["counts"]),
        "research": None,
    }
    if case == "write":
        try:
            actual = json.loads((root / "summary.json").read_text())
            checks[case] = actual == {
                "project": expected["project"],
                "total": expected["total"],
            } and any(c in calls for c in ("create_file", "write_file"))
        except (OSError, ValueError):
            checks[case] = False
    if case == "report":
        checks[case] = (
            (root / "report.md").exists()
            and "write_report" in calls
            and any(
                a["current_task"] and a["current"] and a["kind"] == "report"
                for a in result.get("artifacts", [])
            )
        )
    if case in {"multi", "dependency"}:
        workers = result.get("workers", [])
        checks[case] = (
            checks[case]
            and len(workers) >= 2
            and all(
                r["status"] == "completed" and r.get("review", {}).get("disposition") == "accepted"
                for r in workers
            )
        )
        if case == "dependency":
            checks[case] = checks[case] and any(r.get("depends_on") for r in workers)
    grade = checks.get(case)
    return {
        "passed": ready and grade if grade is not None else None,
        "review_required": grade is None,
        "runtime_completed": ready,
        "task_success_distinct": True,
        "tool_calls": calls,
    }


def evaluate(args, key):
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    rows = []
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cases = args.cases.split(",")
    if not models or any(case not in TASKS for case in cases):
        raise ValueError("Specify model IDs and known cases")
    for model in models:
        for repeat in range(args.rounds):
            for case in cases:
                index = len(rows) + 1
                # Holdout contents change across trials. A repeated memorized answer fails.
                counts = [17 + repeat, 24 + 2 * repeat]
                project = "Flora trial " + str(index)
                with tempfile.TemporaryDirectory(prefix="flora-eval-") as temporary:
                    root = Path(temporary)
                    fixture = {"project": project, "records": [{"count": n} for n in counts]}
                    atomic_json(root / "evidence.json", fixture)
                    atomic_json(root / "left.json", {"count": counts[0]})
                    atomic_json(root / "right.json", {"count": counts[1]})
                    (root / "sales.csv").write_text(
                        "name,amount\nleft," + str(counts[0]) + "\nright," + str(counts[1]) + "\n"
                    )
                    profile = read_profile(args.profile) if args.profile else {}
                    profile.setdefault("provider", {}).update(
                        model=model,
                        base_url=args.base_url,
                        api_key_env=None,
                        allow_insecure_http=args.base_url.startswith("http:"),
                        timeout=30,
                        total_timeout=120,
                        max_tokens_parameter="max_tokens",
                    )
                    profile.setdefault("compiler", {}).update(compilation_timeout=120)
                    profile["budget"] = {
                        "max_model_calls": 12,
                        "max_tool_calls": 40,
                        "max_input_tokens": 500000,
                        "max_output_tokens": 180000,
                        "max_wall_seconds": 600,
                    }
                    profile.setdefault("general", {}).update(
                        subagents={
                            "enabled": case in {"multi", "dependency"},
                            "budget": {
                                "max_model_calls": 6,
                                "max_tool_calls": 20,
                                "max_wall_seconds": 300,
                            },
                        }
                    )
                    events = []
                    start = time.monotonic()
                    try:
                        with GeneralAgent(
                            session_dir=output / f"session-{index:04d}",
                            workspace=root,
                            profile=profile,
                            session_key=key,
                            on_event=events.append,
                        ) as app:
                            result = app.run(TASKS[case])
                            result["workers"] = (
                                app.delegation.agent_status()["agents"] if app.delegation else []
                            )
                            unchanged = json.loads((root / "evidence.json").read_text()) == fixture
                            grade = assess(
                                case,
                                result,
                                root,
                                events,
                                {"counts": counts, "total": sum(counts), "project": project},
                            )
                            if not unchanged:
                                grade["passed"] = False
                            # Public artifact contains no prompts, secret or raw provider body.
                            row = {
                                "model": model,
                                "case": case,
                                "trial": repeat + 1,
                                "status": result["status"],
                                "failure": result.get("failure"),
                                "budget": result.get("budget"),
                                "grade": grade,
                                "fixture_unchanged": unchanged,
                            }
                    except Exception as exc:
                        row = {
                            "model": model,
                            "case": case,
                            "trial": repeat + 1,
                            "status": "probe_error",
                            "exception_type": type(exc).__name__,
                            "failure": failure_info("needs_program", type(exc).__name__),
                            "grade": {"passed": False, "runtime_completed": False},
                        }
                    row["seconds"] = round(time.monotonic() - start, 3)
                    rows.append(row)
                    atomic_json(
                        output / "evaluation.json",
                        {
                            "date": datetime.now(UTC).isoformat(),
                            "base_url": args.base_url,
                            "model_selection": "explicit IDs; gateway advertisement is not verified model provenance",
                            "results": rows,
                            "scope": "real model/API; deterministic task oracles; research needs human review",
                        },
                    )
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default=",".join(TASKS))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error("rounds must be 1..5")
    key = getpass.getpass("API key (hidden; memory only): ")
    rows = evaluate(args, key)
    failures = [r for r in rows if r["grade"].get("passed") is False]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
