"""Opt-in DeepSeek/GLM evaluation; never called by ordinary test discovery.

PYTHONPATH=src python -m tests.live_reliability_probe --base-url HTTPS_URL
--models deepseek-ID,glm-ID --output /outside/repository --cases stress
--api-key-env FLORA_API_KEY

Use explicit gateway model IDs. Family labels are not proof of model provenance.
No discovery, live preflight or model request occurs merely by importing this module.
Exit codes: 0 all graded passes; 1 failures/unrun; 2 completed answers needing review.
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import hashlib
import json
import os
import random
import re
import sqlite3
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from flora.general.agent import GeneralAgent, read_profile
from flora.general.reliability import failure_info
from flora.general.storage import atomic_json
from flora.state.trace import MemoryTrace
from flora.support.values import digest

if __package__:
    from tests.stress_cases import STRESS_TASKS, make_stress_fixture
else:  # Preserve the documented direct-script invocation as well as python -m.
    from stress_cases import STRESS_TASKS, make_stress_fixture

TASKS = {
    "greet": "hello",
    "compute": "Compute (17 + 24) * 3. Return only the number.",
    "read": "Read evidence.json and return the project field and sum of counts as an object with project and total.",
    "write": "Read evidence.json and create summary.json with project and the sum of counts under total. Do not modify evidence.json. Return only the file path.",
    "branch": "Read optional.json if present and return its project. Only if missing, read evidence.json and return its project. Do not create anything.",
    "table": "Use sales.csv to compute the sum of the amount column. Return only the exact total.",
    "multi": "Use two independent read-only subagents: one reads left.json, the other right.json. Each returns only {count: the observed count, path: the file path}. Collect their complete actual results, inspect the two original files, review both workers, and return the sum as a number. Do not guess or claim success before checking both results.",
    "dependency": "Delegate to a child that reads left.json and returns only {count: the observed count, path: 'left.json'}. Delegate to another child with depends_on the first; ask it to compare that actual dependency answer to right.json and return only {left: the dependency count, right: the observed right count, maximum: the larger count}. Collect and review both actual answers, inspect source files yourself, and return the larger count as a number.",
    "report": "Read evidence.json and produce report.md using write_report. Include exactly one line 'Project: ' followed by the project, and exactly one line 'Total: ' followed by the exact total. Cite an observed source of evidence.json. Return only the real publication path.",
    "research": "Research the most recent available official documentation on agent evaluation. Use web search and read the actual official pages, use read-only workers when useful, return evidence-linked findings and unresolved limitations. Never present blocked access as successful research.",
    **STRESS_TASKS,
}
BASE_CASES = [name for name in TASKS if name not in STRESS_TASKS]
COLLABORATIVE = {"multi", "dependency", "dependency_route"}
COUNTERS = ("model_calls", "tool_calls", "input_tokens", "output_tokens", "unknown_usage_calls")
READ_TOOLS = {"read_file", "read_document", "table_query", "search_files"}
PARENT_LIMITS = {
    "max_model_calls": 12,
    "max_tool_calls": 80,
    "max_input_tokens": 500000,
    "max_output_tokens": 180000,
    "max_wall_seconds": 600,
}
CHILD_LIMITS = {
    "max_model_calls": 6,
    "max_tool_calls": 40,
    "max_input_tokens": 250000,
    "max_output_tokens": 90000,
    "max_wall_seconds": 300,
}


def apply_evaluation_limits(profile):
    """Fill missing operational bounds; explicit limits, including None, remain authoritative."""
    parent = profile.setdefault("budget", {})
    child = profile.setdefault("general", {}).setdefault("subagents", {}).setdefault("budget", {})
    for actual, defaults in ((parent, PARENT_LIMITS), (child, CHILD_LIMITS)):
        for name, value in defaults.items():
            actual.setdefault(name, value)
    return {"parent": dict(parent), "per_child": dict(child)}


def equal(actual, expected):
    """JSON value equality; numeric formatting may vary, bool is never a number."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            equal(actual[k], expected[k]) for k in actual
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def selected_models(text):
    models = [part.strip() for part in text.split(",") if part.strip()]
    if not models or len(set(models)) != len(models):
        raise ValueError("Specify distinct explicit DeepSeek/GLM model IDs")
    for model in models:
        if re.search(r"gpt|claude", model, re.I) or not re.search(
            r"(?:^|/)(?:deepseek(?:[-_.:]|$)|glm(?:[-_.:0-9]|$))", model, re.I
        ):
            raise ValueError("Only explicitly selected DeepSeek and GLM family IDs are allowed")
    return models


def selected_cases(text):
    names = (
        list(STRESS_TASKS)
        if text == "stress"
        else list(TASKS)
        if text == "all"
        else text.split(",")
    )
    if not names or len(set(names)) != len(names) or any(name not in TASKS for name in names):
        raise ValueError("Specify distinct known cases, stress, or all")
    return names


def read_receipts(path):
    """Read/validate the actual journal without changing it or model-visible state."""
    if path is None:
        return []
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        entries = [
            json.loads(row[0]) for row in db.execute("SELECT entry FROM journal ORDER BY seq")
        ]
    return MemoryTrace.from_dict({"format": "openharness-trace-v1", "journal": entries}).records


def collect_evidence(app, result):
    result["_receipts"] = read_receipts(app.agent.current_trace_path)
    workers = app.delegation.agent_status()["agents"] if app.delegation else []
    for worker in workers:
        ident = worker["id"]
        worker["_view"] = app.delegation._result_view(ident)
        worker["_receipts"] = [
            receipt
            for path in sorted((app.delegation.root / ident / "kernel").glob("turn-*.sqlite"))
            for receipt in read_receipts(path)
        ]
    result["workers"] = workers
    sources, offset = [], 0
    while True:
        page = app.store.list_sources(offset=offset, limit=100)
        sources.extend(page["sources"])
        offset = page["next_offset"]
        if offset is None:
            break
    result["_sources"] = sources


def mechanism_observations(events):
    """Observed kernel activity, not evidence of synthesis quality or causal benefit."""
    records = [e.get("event", {}) if e.get("kind") == "subagent_event" else e for e in events]
    kinds = ("diagnostic_evaluated", "forecast_observation", "consumer_check", "revision_checked")
    values = {kind: sum(e.get("kind") == kind for e in records) for kind in kinds}
    values["multiple_candidate_bundles"] = sum(
        e.get("kind") == "bundle_installed" and len(e.get("normal_candidates", [])) > 1
        for e in records
    )
    values["semantic_or_causal_validation"] = False
    return values


def aggregate_usage(result):
    """Budgets are independent. Never count the child's result and registry twice."""
    ledgers = [result.get("budget", {})] + [w.get("budget", {}) for w in result.get("workers", [])]
    return {name: sum(b.get(name, 0) for b in ledgers) for name in COUNTERS}


def summary(rows):
    """Keep families/models separate; review-required and unrun are never successes."""
    result = []
    for model, case in sorted({(r["model"], r["case"]) for r in rows}):
        group = [r for r in rows if r["model"] == model and r["case"] == case]
        attempted = [r for r in group if r["status"] != "not_run"]
        graded = [r for r in attempted if r["grade"].get("passed") is not None]
        seconds = sorted(r["seconds"] for r in attempted)
        result.append(
            {
                "model": model,
                "case": case,
                "planned": len(group),
                "attempted": len(attempted),
                "unrun": len(group) - len(attempted),
                "graded": len(graded),
                "review_required": len(attempted) - len(graded),
                "first_passes": sum(r.get("first_pass") is True for r in graded),
                "final_passes": sum(r["grade"]["passed"] is True for r in graded),
                "runtime_completions": sum(
                    r["grade"].get("runtime_completed") is True for r in attempted
                ),
                "median_seconds": statistics.median(seconds) if seconds else None,
                "p95_seconds": seconds[max(0, (95 * len(seconds) + 99) // 100 - 1)]
                if seconds
                else None,
                "aggregate_usage": {
                    name: sum(r.get("aggregate_usage", {}).get(name, 0) for r in attempted)
                    for name in COUNTERS
                },
            }
        )
    return result


def workspace_path(path, *, directory=False):
    """Normalize only aliases accepted by workspace tools, never traversal/absolute paths."""
    if not isinstance(path, str) or "\\" in path or "\0" in path:
        return None
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return None
    parts = path.split("/")
    if ".." in parts:
        return None
    parts = [part for part in parts if part not in {"", "."}]
    return "/".join(parts) if parts else "." if directory else None


def receipt_path(receipt):
    requested = workspace_path(receipt.get("args", {}).get("path"))
    if requested is None:
        return None
    value = receipt.get("value")
    returned = workspace_path(value.get("path")) if isinstance(value, dict) else None
    return returned or requested


def path_value_equal(actual, expected):
    """Task schemas identifying files accept ordinary relative-path aliases."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        actual = dict(actual)
        for field in ("path", "file"):
            if field in expected and field in actual:
                actual[field] = workspace_path(actual[field])
    return equal(actual, expected)


def observed_paths(receipts):
    paths = set()
    for receipt in receipts:
        if receipt.get("status") != "returned":
            continue
        if receipt.get("tool") == "search_files":
            value = receipt.get("value", {})
            if isinstance(value, dict):
                paths.update(
                    workspace_path(m.get("path"))
                    for m in value.get("matches", [])
                    if isinstance(m, dict)
                )
        elif receipt.get("tool") in READ_TOOLS:
            paths.add(receipt_path(receipt))
    return paths - {None}


def search_covers_forbidden(receipt, forbidden):
    """Conservative scope check: search can read files even when it returns no matches.

    Restricted only to explicitly forbidden fixture files. Match/count limits do not
    prove a file was never scanned, so they cannot excuse an overlapping search scope.
    """
    if receipt.get("tool") != "search_files":
        return False
    args = receipt.get("args", {})
    scope = workspace_path(args.get("path", "."), directory=True)
    pattern, depth = args.get("glob", "*"), args.get("max_depth", 8)
    if scope is None or not isinstance(pattern, str) or type(depth) is not int or depth < 0:
        return False
    prefix = "" if scope == "." else scope + "/"
    return any(
        path.startswith(prefix)
        and len(path[len(prefix) :].split("/")) - 1 <= depth
        and fnmatch.fnmatchcase(path, pattern)
        for path in forbidden
    )


def path_answer(value, root, expected):
    if not isinstance(value, str) or ".." in value.split("/"):
        return False
    # User-facing publication paths may be absolute, but only inside this workspace.
    if value.startswith("/"):
        try:
            value = str(Path(value).relative_to(root))
        except ValueError:
            return False
    return workspace_path(value) == expected


def reviewed(worker):
    view = worker.get("_view")
    if not view or worker.get("status") != "completed" or view.get("status") != "completed":
        return False
    length = len(json.dumps(view, ensure_ascii=False, separators=(",", ":")))
    review = worker.get("review") or {}
    return (
        review.get("disposition") == "accepted"
        and review.get("result_digest") == digest(view) == worker.get("read_digest")
        and worker.get("read_windows") == [[0, length]]
    )


def collaboration_checks(case, result, expected):
    workers = result.get("workers", [])
    if len(workers) != 2 or len({w.get("id") for w in workers}) != 2:
        return False
    if not all(reviewed(worker) for worker in workers):
        return False
    parent_paths = observed_paths(result.get("_receipts", []))
    if case == "multi":
        for path, count in zip(("left.json", "right.json"), expected["counts"], strict=True):
            matches = [
                w
                for w in workers
                if path_value_equal(w["_view"].get("value"), {"count": count, "path": path})
                and path in observed_paths(w.get("_receipts", []))
                and not w.get("depends_on")
            ]
            if len(matches) != 1:
                return False
        return {"left.json", "right.json"} <= parent_paths
    if case == "dependency":
        first_answer = {"count": expected["counts"][0], "path": "left.json"}
        second_answer = {
            "left": expected["counts"][0],
            "right": expected["counts"][1],
            "maximum": max(expected["counts"]),
        }
        first_path, second_path = "left.json", "right.json"
    else:
        first_answer = {"file": expected["selected"], "nonce": expected["answer"]["nonce"]}
        second_answer = expected["answer"]
        first_path, second_path = "route.json", expected["selected"]
    first = next(
        (
            w
            for w in workers
            if path_value_equal(w["_view"].get("value"), first_answer)
            and first_path in observed_paths(w.get("_receipts", []))
            and not w.get("depends_on")
        ),
        None,
    )
    second = next(
        (
            w
            for w in workers
            if equal(w["_view"].get("value"), second_answer)
            and second_path in observed_paths(w.get("_receipts", []))
        ),
        None,
    )
    return bool(
        first
        and second
        and first is not second
        and second.get("depends_on") == [first["id"]]
        and {first_path, second_path} <= parent_paths
    )


def assess(case, result, root, events, expected):
    """External task-specific oracle; never exposed as a model tool or runtime gate.

    Structural evidence is necessary, not proof of arbitrary semantic correctness.
    Free-form research still needs an independent human review.
    """
    value, receipts = result.get("value"), result.get("_receipts", [])
    calls = [r.get("tool") for r in receipts]
    paths = observed_paths(receipts)
    ready = result.get("status") == "completed"
    provenance = not any(r.get("status") in {"pending", "interrupted_unknown"} for r in receipts)
    checks = {
        "greet": isinstance(value, str)
        and bool(re.search(r"hello|hi\b|你好", value, re.I))
        and not calls,
        "compute": equal(value, 123) or value == "123",
        "research": None,
    }
    if case in {"read", "write", "report", "branch", "table", "multi", "dependency"}:
        answer = {"project": expected["project"], "total": expected["total"]}
        if case == "read":
            checks[case] = equal(value, answer)
            provenance &= "evidence.json" in paths
        elif case == "branch":
            checks[case] = value == expected["branch_project"]
            optional = [
                r
                for r in receipts
                if workspace_path(r.get("args", {}).get("path")) == "optional.json"
                and r.get("tool") in READ_TOOLS - {"search_files"}
            ]
            provenance &= bool(optional)
            if expected["optional_present"]:
                provenance &= "optional.json" in paths and "evidence.json" not in paths
            else:
                provenance &= any(r.get("status") == "raised" for r in optional)
                provenance &= "evidence.json" in paths
        elif case == "table":
            checks[case] = equal(value, expected["total"]) or value == str(expected["total"])
            provenance &= "sales.csv" in paths
        elif case == "write":
            try:
                checks[case] = equal(json.loads((root / "summary.json").read_text()), answer)
            except (OSError, ValueError):
                checks[case] = False
            provenance &= "evidence.json" in paths and path_answer(value, root, "summary.json")
            provenance &= any(
                r.get("status") == "returned"
                and r.get("tool") in {"create_file", "write_file"}
                and receipt_path(r) == "summary.json"
                for r in receipts
            )
        elif case == "report":
            try:
                content = (root / "report.md").read_text(encoding="utf-8")
                body = content.split("\n## Sources\n", 1)[0]
                checks[case] = re.findall(r"^Project: (.*)$", body, re.M) == [expected["project"]]
                checks[case] &= re.findall(r"^Total: (.*)$", body, re.M) == [str(expected["total"])]
                cited = set(re.findall(r"\[(src-[0-9]+)\]", body))
                originals = {
                    s["source_id"]
                    for s in result.get("_sources", [])
                    if isinstance(s.get("origin"), str)
                    and s["origin"].startswith("workspace:")
                    and workspace_path(s["origin"][len("workspace:") :]) == "evidence.json"
                    and s.get("sha256") == expected["input_hashes"]["evidence.json"]
                }
                provenance &= bool(cited & originals)
                sha = hashlib.sha256((root / "report.md").read_bytes()).hexdigest()
                provenance &= any(
                    workspace_path(a.get("path")) == "report.md"
                    and a.get("sha256") == sha
                    and a.get("current_task")
                    and a.get("current")
                    and a.get("kind") == "report"
                    and set(a.get("sources", [])) == cited
                    for a in result.get("artifacts", [])
                )
            except (OSError, ValueError):
                checks[case] = False
            provenance &= "write_report" in calls and path_answer(value, root, "report.md")
        else:
            checks[case] = equal(
                value, expected["total"] if case == "multi" else max(expected["counts"])
            )
    elif case in STRESS_TASKS:
        checks[case] = (
            path_value_equal(value, expected["answer"])
            if case == "edit_preserve"
            else equal(value, expected["answer"])
        )
        provenance &= set(expected["required_reads"]) <= paths
        for path, target in expected.get("files", {}).items():
            try:
                checks[case] &= equal(json.loads((root / path).read_text()), target)
            except (OSError, ValueError):
                checks[case] = False
        if expected.get("readback"):
            path = expected["readback"]
            writes = [
                i
                for i, r in enumerate(receipts)
                if r.get("status") == "returned"
                and r.get("tool") in {"write_file", "update_file", "replace_text"}
                and receipt_path(r) == path
            ]
            provenance &= bool(writes) and path in observed_paths(receipts[writes[-1] + 1 :])
    if case in COLLABORATIVE:
        provenance &= collaboration_checks(case, result, expected)
    all_receipts = receipts + [r for w in result.get("workers", []) for r in w.get("_receipts", [])]
    requested_paths = {
        workspace_path(r.get("args", {}).get("path")) for r in all_receipts
    } | observed_paths(all_receipts)
    forbidden = set(expected.get("forbidden_reads", []))
    provenance &= not bool(forbidden & requested_paths)
    provenance &= not any(search_covers_forbidden(r, forbidden) for r in all_receipts)
    if "input_hashes" in expected:
        provenance &= unchanged_inputs(root, expected)
    grade = checks.get(case)
    return {
        "passed": bool(ready and grade and provenance)
        if grade is not None
        else None
        if ready and provenance
        else False,
        "review_required": grade is None and ready and bool(provenance),
        "runtime_completed": ready,
        "answer_correct": grade,
        "evidence_integrity": bool(provenance),
        "task_success_distinct": True,
        "dependency_causal_use_verified": False,
        "tool_calls": calls,
    }


def snapshot(root):
    """File hashes plus explicit directory markers, including empty directories."""
    values = {}
    for path in root.rglob("*"):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            values[relative] = "SYMLINK"
        elif path.is_dir():
            values[relative + "/"] = "DIRECTORY"
        elif path.is_file():
            values[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            values[relative] = "SPECIAL"
    return values


def unchanged_inputs(root, expected):
    current = snapshot(root)
    old = expected["input_hashes"]
    return (
        all(
            current.get(path) == sha
            for path, sha in old.items()
            if path not in expected.get("allowed_changes", [])
        )
        and set(current) - set(old) <= set(expected.get("allowed_new", []))
        and not any(p.is_symlink() for p in root.rglob("*"))
    )


def prepare_case(case, root, seed):
    if case in STRESS_TASKS:
        expected = make_stress_fixture(case, root, seed)
    else:
        rng = random.Random(seed)
        counts = [rng.randint(-100, 900), rng.randint(-100, 900)]
        project = "Flora " + hashlib.sha256(str(seed).encode()).hexdigest()[:12]
        fixture = {"project": project, "records": [{"count": n} for n in counts]}
        atomic_json(root / "evidence.json", fixture)
        atomic_json(root / "left.json", {"count": counts[0]})
        atomic_json(root / "right.json", {"count": counts[1]})
        (root / "sales.csv").write_text(f"name,amount\nleft,{counts[0]}\nright,{counts[1]}\n")
        present = bool(seed % 2)
        preferred = "Preferred " + project
        if case == "branch" and present:
            atomic_json(root / "optional.json", {"project": preferred})
        expected = {
            "counts": counts,
            "total": sum(counts),
            "project": project,
            "optional_present": present,
            "branch_project": preferred if present else project,
            "allowed_new": {"write": ["summary.json"], "report": ["report.md"]}.get(case, []),
        }
    expected["input_hashes"] = snapshot(root)
    return expected


def validate_options(args):
    models, cases = selected_models(args.models), selected_cases(args.cases)
    url = urlsplit(args.base_url)
    if (
        url.scheme not in {"https", "http"}
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Use an HTTP(S) base URL without embedded credentials, query or fragment")
    if url.scheme == "http" and not getattr(args, "allow_insecure_http", False):
        raise ValueError("Plain HTTP requires explicit --allow-insecure-http; prefer HTTPS")
    if not 1 <= args.rounds <= 20 or not 0 <= getattr(args, "resume_attempts", 0) <= 2:
        raise ValueError("rounds must be 1..20 and resume-attempts 0..2")
    output, repository = args.output.resolve(), Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        raise ValueError("Evaluation output must be outside the repository")
    return models, cases


def evaluate(args, key):
    models, cases = validate_options(args)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    rows = []
    unavailable_models = set()
    seed = getattr(args, "seed", 20261001)
    order = [
        (model, repeat, case) for model in models for repeat in range(args.rounds) for case in cases
    ]
    random.Random(seed).shuffle(order)
    for index, (model, repeat, case) in enumerate(order, start=1):
        # The same case/repeat seed is used across models and paired implementations.
        fixture_seed = (
            int(hashlib.sha256(f"{seed}:{repeat}:{case}".encode()).hexdigest()[:12], 16) << 1
        ) | (repeat % 2)
        if model in unavailable_models:
            rows.append(
                {
                    "model": model,
                    "case": case,
                    "trial": repeat + 1,
                    "fixture_seed": fixture_seed,
                    "status": "not_run",
                    "seconds": 0,
                    "reason": "Earlier model transport failure; fix endpoint before retrying",
                    "grade": {"passed": None, "runtime_completed": False, "review_required": False},
                }
            )
            continue
        case_dir = output / f"case-{index:04d}"
        root = case_dir / "workspace"
        root.mkdir(parents=True)
        expected = prepare_case(case, root, fixture_seed)
        atomic_json(case_dir / "oracle.json", expected)  # Outside the agent's file root.
        profile = read_profile(args.profile) if args.profile else {}
        profile.setdefault("provider", {}).update(
            model=model,
            base_url=args.base_url,
            api_key_env=None,
            allow_insecure_http=getattr(args, "allow_insecure_http", False),
            timeout=30,
            total_timeout=120,
            max_tokens_parameter="max_tokens",
        )
        profile.setdefault("compiler", {}).update(compilation_timeout=120)
        # Operational bounds prevent runaway calls; explicit profile limits/None are preserved.
        profile.setdefault("general", {}).update(allow_commands=False)
        profile["general"].setdefault("subagents", {})["enabled"] = case in COLLABORATIVE
        resolved_limits = apply_evaluation_limits(profile)
        events, attempts = [], []
        start = time.monotonic()
        result, app = {}, None
        phase = "initialize"
        try:
            app = GeneralAgent(
                session_dir=case_dir / "session",
                workspace=root,
                profile=profile,
                session_key=key,
                on_event=events.append,
            )
            for attempt in range(getattr(args, "resume_attempts", 0) + 1):
                phase = "run"
                result = {}
                result = app.run(TASKS[case]) if attempt == 0 else app.resume()
                # Legacy protocols omit application failure metadata. Derive only sanitized advice.
                result["failure"] = result.get("failure") or failure_info(
                    result["status"], result.get("reason", "")
                )
                phase = "collect_evidence"
                collect_evidence(app, result)
                phase = "assess"
                grade = assess(case, result, root, events, expected)
                intact = unchanged_inputs(root, expected)
                if not intact:
                    grade["passed"] = False
                    grade["evidence_integrity"] = False
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "status": result["status"],
                        "grade": grade,
                        "aggregate_usage": aggregate_usage(result),
                    }
                )
                if (result.get("failure") or {}).get("code") == "model_transport":
                    break  # Compiler transport retries are already bounded and charged.
                if result["status"] not in {"needs_program", "stalled", "incomplete"}:
                    break
            row = {
                "status": result["status"],
                "runtime_status": result["status"],
                "failure": result.get("failure"),
                "budget": result.get("budget"),
                "aggregate_usage": aggregate_usage(result),
                "grade": grade,
                "fixture_unchanged": intact,
                "first_pass": attempts[0]["grade"]["passed"],
                "attempts": attempts,
                "output_hashes": snapshot(root),
            }
        except Exception as exc:
            row = {
                "status": "probe_error",
                "exception_type": type(exc).__name__,
                "failure_phase": phase,
                "runtime_status": result.get("status"),
                "failure": result.get("failure"),
                "grade": {
                    "passed": False,
                    "runtime_completed": result.get("status") == "completed",
                    "review_required": False,
                },
                "attempts": attempts,
            }
        finally:
            if app:
                app.close()
                # Capture final worker accounting after close, even for interrupted cases.
                result["workers"] = (
                    app.delegation.agent_status()["agents"] if app.delegation else []
                )
                result["budget"] = app.agent.status()["budget"]
                row["aggregate_usage"] = aggregate_usage(result)
        if (row.get("failure") or {}).get("code") == "model_transport" and not getattr(
            args, "continue_on_transport_error", False
        ):
            unavailable_models.add(model)
        row["resolved_limits"] = resolved_limits
        row["mechanism_observations"] = mechanism_observations(events)
        row.update(
            model=model,
            case=case,
            trial=repeat + 1,
            fixture_seed=fixture_seed,
            seconds=round(time.monotonic() - start, 3),
        )
        rows.append(row)
        manifest = {
            "date": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "seed": seed,
            "model_selection": "explicit DeepSeek/GLM IDs; model provenance not independently verified",
            "results": rows,
            "summary": summary(rows),
            "scope": "synthetic task families; research requires human review; no proof of spontaneous diagnostic/contract synthesis or causal benefit",
            "cost": "aggregate parent plus workers; token usage is not verified currency cost",
        }
        atomic_json(output / "evaluation.json", manifest)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    # Persist skipped tail rows as well; every planned trial remains in the denominator ledger.
    manifest["results"] = rows
    manifest["summary"] = summary(rows)
    atomic_json(output / "evaluation.json", manifest)
    return rows


def exit_status(rows):
    if not rows or any(
        r.get("status") == "not_run" or r["grade"].get("passed") is False for r in rows
    ):
        return 1
    return 2 if any(r["grade"].get("passed") is None for r in rows) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--models", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default=",".join(BASE_CASES))
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--resume-attempts", type=int, default=0)
    parser.add_argument("--profile", type=Path)
    parser.add_argument(
        "--api-key-env", help="Explicit environment variable; otherwise hidden interactive input"
    )
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument(
        "--continue-on-transport-error",
        action="store_true",
        help="Explicitly retry later cases after an exhausted model transport failure",
    )
    args = parser.parse_args()
    try:
        validate_options(args)  # Reject unsupported models/transport before touching credentials.
    except ValueError as exc:
        parser.error(str(exc))
    key = (
        os.environ.get(args.api_key_env, "")
        if args.api_key_env
        else getpass.getpass("API key (hidden; memory only): ")
    )
    if not key:
        parser.error("No API key provided")
    rows = evaluate(args, key)
    return exit_status(rows)


if __name__ == "__main__":
    raise SystemExit(main())
