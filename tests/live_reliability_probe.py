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
import subprocess
import threading
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
COLLABORATIVE = {"multi", "dependency", "dependency_route", "release_audit"}
COUNTERS = ("model_calls", "tool_calls", "input_tokens", "output_tokens", "unknown_usage_calls")
READ_TOOLS = {"read_file", "read_lines", "read_document", "table_query", "search_files"}
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


def source_provenance():
    """Only source files and Git metadata; never hash credentials or environment values."""
    repository = Path(__file__).resolve().parents[1]
    files = {}
    for directory in ("src", "tests"):
        for path in sorted((repository / directory).rglob("*.py")):
            if path.is_symlink() or not path.resolve().is_relative_to(repository):
                continue
            files[str(path.relative_to(repository))] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=repository,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        head, dirty = None, None
    return {"git_head": head, "tracked_dirty": dirty, "python_source_sha256": files}


def configuration_view(profile):
    """Record typed operational choices, never arbitrary request options or credentials."""
    provider = profile.get("provider", {})
    numeric = (
        "timeout",
        "total_timeout",
        "progress_timeout",
        "first_program_timeout",
        "max_response_bytes",
        "max_stream_bytes",
        "max_json_whitespace",
    )
    view = {k: provider[k] for k in numeric if type(provider.get(k)) in (int, float)}
    for k in ("stream", "stream_fallback", "stream_idle_fallback", "allow_insecure_http"):
        if type(provider.get(k)) is bool:
            view[k] = provider[k]
    options = provider.get("request_options", {})
    if isinstance(options, dict):
        for name, allowed in (("reasoning_effort", {"none", "low", "medium", "high", "max"}),):
            if isinstance(options.get(name), str) and options[name] in allowed:
                view[name] = options[name]
        for name, allowed in (
            ("thinking", {"enabled", "disabled"}),
            ("response_format", {"json_object", "text", "json_schema"}),
        ):
            value = options.get(name)
            if (
                isinstance(value, dict)
                and isinstance(value.get("type"), str)
                and value["type"] in allowed
            ):
                view[name] = value["type"]
    compiler = {
        k: v
        for k, v in profile.get("compiler", {}).items()
        if k
        in {
            "max_output_tokens",
            "max_repairs",
            "compilation_timeout",
            "max_context_bytes",
            "max_output_bytes",
        }
        and (v is None or type(v) in (int, float))
    }
    version = profile.get("general", {}).get("tool_schema_version")
    general = {}
    require_task_completion = profile.get("general", {}).get("require_task_completion")
    if type(require_task_completion) is bool:
        general["require_task_completion"] = require_task_completion
    for name, allowed in (
        ("syntax", {"ir-v1", "observe-v1", "block-list-v1", "block-list-v2", "block-list-v3"}),
        ("prompt_style", {"full-v1", "compact-v1", "compact-v2", "compact-v3"}),
    ):
        value = profile.get("compiler", {}).get(name)
        if isinstance(value, str) and value in allowed:
            compiler[name] = value
    return {
        "provider": view,
        "compiler": compiler,
        "general": general,
        "tool_schema_version": version
        if type(version) is int and version in (1, 2, 3, 4)
        else None,
    }


class AccessFailureMonitor:
    """Pause parent and workers when their real event stream reports access denial."""

    def __init__(self, events, *, pause_after_write=False):
        self.events = events
        self.app = None
        self.status = None
        self.pause_after_write = pause_after_write
        self.write_paused = False
        self.started = time.monotonic()
        self._telemetry_lock = threading.Lock()
        self.first_tool_seconds = None
        self.program_bytes = {}
        self.validation_rejections = 0
        self.replan_count = 0

    def telemetry(self):
        """Operational counts only; generated text is neither retained nor interpreted."""
        with self._telemetry_lock:
            sizes = list(self.program_bytes.values())
            return {
                "first_tool_dispatch_seconds": self.first_tool_seconds,
                "generated_program_text_bytes": sum(sizes),
                "largest_generated_program_text_bytes": max(sizes, default=0),
                "generations_with_program_text": len(sizes),
                "validation_rejections": self.validation_rejections,
                "replans": self.replan_count,
                "scope": "Observer wall time and UTF-8 program transcript chunks; not tokens, lowered IR size, or causal evidence",
            }

    def __call__(self, event):
        self.events.append(event)
        with self._telemetry_lock:
            if event.get("kind") == "transcript":
                channel = event.get("channel")
                if channel == "tool/call" and self.first_tool_seconds is None:
                    self.first_tool_seconds = round(time.monotonic() - self.started, 3)
                elif channel == "program":
                    key = (event.get("actor"), event.get("request"))
                    self.program_bytes[key] = self.program_bytes.get(key, 0) + len(
                        event.get("text", "").encode("utf-8")
                    )
                elif channel == "compiler_rejected":
                    self.validation_rejections += 1
            actual = event.get("event", {}) if event.get("kind") == "subagent_event" else event
            if actual.get("kind") == "replan_requested":
                self.replan_count += 1
        if (
            self.pause_after_write
            and not self.write_paused
            and self.app
            and event.get("kind") == "tool_result"
            and event.get("status") == "returned"
            and event.get("tool") in {"create_file", "write_file", "update_file", "write_report"}
        ):
            self.write_paused = True
            self.app.request_pause()
        failure = event
        if event.get("kind") == "transcript" and event.get("channel") == "model_failure":
            try:
                failure = json.loads(event["text"])
            except (ValueError, KeyError):
                return
        if (
            isinstance(failure, dict)
            and failure.get("kind") == "model_failure"
            and failure.get("http_status") in {401, 402, 403, 404}
        ):
            self.status = failure["http_status"]
            if self.app:
                self.app.request_pause()


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
    kinds = (
        "diagnostic_evaluated",
        "forecast_observation",
        "consumer_check",
        "revision_checked",
        "reuse_checked",
        "diagnostic_inapplicable",
        "invalid_diagnostic",
    )
    values = {kind: sum(e.get("kind") == kind for e in records) for kind in kinds}
    values["multiple_candidate_bundles"] = sum(
        e.get("kind") == "bundle_installed" and len(e.get("normal_candidates", [])) > 1
        for e in records
    )
    values["inserted_diagnostic_actions"] = sum(
        e.get("kind") == "action_selected" and e.get("diagnostic") is True for e in records
    )
    values["accepted_revisions"] = sum(
        e.get("kind") == "revision_checked" and e.get("accepted") is True for e in records
    )
    values["rejected_reuse"] = sum(
        e.get("kind") == "reuse_checked" and e.get("accepted") is False for e in records
    )
    values["omitted_reports"] = sum(e.get("kind") == "report_omitted" for e in records)
    revisions = [e for e in records if e.get("kind") == "revision_checked"]
    values["revision_modes"] = {
        mode: {
            "checked": sum(e.get("mode") == mode for e in revisions),
            "accepted": sum(e.get("mode") == mode and e.get("accepted") is True for e in revisions),
        }
        for mode in ("PRESERVE", "EXTEND", "CHANGE")
    }
    values["history_and_current_pass_revisions"] = sum(
        e.get("mode") in {"PRESERVE", "EXTEND"}
        and e.get("accepted") is True
        and bool(e.get("results"))
        and all(r.get("verdict") == "PASS" for r in e["results"])
        and (e.get("current_check") or {}).get("verdict") == "PASS"
        for e in revisions
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
        elif receipt.get("tool") == "read_source":
            value = receipt.get("value", {})
            origin = value.get("origin") if isinstance(value, dict) else None
            if isinstance(origin, str) and origin.startswith("workspace:"):
                paths.add(workspace_path(origin[len("workspace:") :]))
            elif isinstance(origin, str) and origin.startswith("table:"):
                paths.add(workspace_path(origin[len("table:") :].rsplit(":", 1)[0]))
    return paths - {None}


# These predicates inspect observed tool records only. They never provide feedback
# to the model and never turn a revision hash into proof of content observation.
WRITE_TOOLS = {
    "create_file",
    "write_file",
    "update_file",
    "replace_text",
    "append_lines",
    "write_report",
}


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _whole_windows(windows, size):
    """Join exact overlapping windows; reject gaps or inconsistent overlaps."""
    if type(size) is not int or size < 0:
        return None
    result = None
    for start, data in sorted(windows, key=lambda item: item[0]):
        if result is None:
            if start != 0:
                return None
            result = data[:0]
        if start > len(result) or start < 0 or start + len(data) > size:
            return None
        overlap = min(len(data), len(result) - start)
        if result[start : start + overlap] != data[:overlap]:
            return None
        result += data[overlap:]
    return result if result is not None and len(result) == size else None


def complete_versions(receipts):
    """Paths mapped to fully observed versions, never unions across revisions.

    File byte/line windows must reconstruct their full-file hash. Extracted source
    windows use the actual source ID, raw input hash and exact character coverage;
    extraction fidelity remains the document tool's responsibility. Complete
    unfiltered table rows count as observed parsed content; arbitrary filtered or
    aggregate queries and search snippets do not establish full content coverage.
    """
    byte_windows, line_windows, source_windows, table_windows = {}, {}, {}, {}
    table_sources = {}
    for receipt in receipts:
        value, args = receipt.get("value"), receipt.get("args", {})
        if receipt.get("status") != "returned" or not isinstance(value, dict):
            continue
        tool = receipt.get("tool")
        if tool == "table_query":
            path, sha = workspace_path(args.get("path")), value.get("input_sha256")
            rows, count, start = (
                value.get("rows"),
                value.get("total_result_rows"),
                args.get("offset", 0),
            )
            if (
                path
                and Path(path).suffix.lower() == ".csv"
                and _sha(sha)
                and not args.get("filters")
                and not args.get("metrics")
                and not args.get("group_by")
                and args.get("sheet") in (None, "", "Sheet1")
                and isinstance(rows, list)
                and all(isinstance(row, dict) for row in rows)
                and type(count) is int
                and count >= 0
                and type(start) is int
                and start >= 0
                and start + len(rows) <= count
                and value.get("matched_rows") == count
                and value.get("rows_in_source") is False
                and value.get("next_offset")
                == (start + len(rows) if start + len(rows) < count else None)
                and isinstance(value.get("source_id"), str)
            ):
                table_windows.setdefault((path, sha, value["source_id"], count), []).append(
                    (start, rows)
                )
            continue
        sha = value.get("sha256")
        if not _sha(sha):
            continue
        path = workspace_path(args.get("path"))
        if tool in {"read_file", "read_lines"}:
            if path is None or workspace_path(value.get("path")) != path:
                continue
            content, size = value.get("content"), value.get("size_bytes")
            if not isinstance(content, str) or type(size) is not int or size < 0:
                continue
            raw = content.encode("utf-8")
            if tool == "read_file":
                start = value.get("offset")
                if (
                    type(start) is not int
                    or start != args.get("offset", 0)
                    or start < 0
                    or value.get("read_bytes") != len(raw)
                    or type(value.get("read_bytes")) is not int
                    or start + len(raw) > size
                ):
                    continue
                more = start + len(raw) < size
                partial = start > 0 or more
                if (
                    value.get("has_more") is not more
                    or value.get("truncated") is not partial
                    or value.get("next_offset") != (start + len(raw) if more else None)
                    or (partial and value.get("partial_sha256") != hashlib.sha256(raw).hexdigest())
                ):
                    continue
                byte_windows.setdefault((path, sha, size), []).append((start, raw))
            else:
                start, end, total = (
                    value.get(k) for k in ("start_line", "end_line", "total_lines")
                )
                lines = content.splitlines(keepends=True)
                if (
                    any(type(n) is not int for n in (start, end, total))
                    or total < 0
                    or start < 1
                    or start != args.get("start_line", 1)
                    or end != start + len(lines) - 1
                    or end > total
                    or value.get("next_line") != (end + 1 if end < total else None)
                    or value.get("truncated") is not (start > 1 or end < total)
                ):
                    continue
                line_windows.setdefault((path, sha, size, total), []).append((start - 1, lines))
        elif tool in {"read_document", "read_source"}:
            origin, ident = value.get("origin"), value.get("source_id")
            if not isinstance(origin, str):
                continue
            table_source = tool == "read_source" and origin.startswith("table:")
            source_path = (
                workspace_path(origin[len("workspace:") :])
                if origin.startswith("workspace:")
                else None
            )
            if (source_path is None and not table_source) or not isinstance(ident, str):
                continue
            if tool == "read_document" and path != source_path:
                continue
            if tool == "read_source" and args.get("source_id") != ident:
                continue
            start, size, text = (value.get(k) for k in ("offset", "total_characters", "text"))
            if (
                type(start) is not int
                or type(size) is not int
                or not isinstance(text, str)
                or start < 0
                or size < 0
                or start + len(text) > size
                or start != (args.get("offset", 0) if tool == "read_source" else 0)
                or value.get("next_offset")
                != (start + len(text) if start + len(text) < size else None)
            ):
                continue
            if table_source:
                table_sources.setdefault((origin, sha, ident, size), []).append((start, text))
            else:
                source_windows.setdefault((source_path, sha, ident, size), []).append((start, text))
    complete = {}
    for (path, sha, size), windows in byte_windows.items():
        content = _whole_windows(windows, size)
        if content is not None and hashlib.sha256(content).hexdigest() == sha:
            complete.setdefault(path, set()).add(sha)
    for (path, sha, size, total), windows in line_windows.items():
        lines = _whole_windows(windows, total)
        if lines is not None:
            content = "".join(lines).encode("utf-8")
            if len(content) == size and hashlib.sha256(content).hexdigest() == sha:
                complete.setdefault(path, set()).add(sha)
    for (path, sha, ident, size), windows in source_windows.items():
        if _whole_windows(windows, size) is not None:
            complete.setdefault(path, set()).add(sha)
    for (path, sha, ident, count), windows in table_windows.items():
        if _whole_windows(windows, count) is not None:
            complete.setdefault(path, set()).add(sha)
    for (origin, sha, ident, size), windows in table_sources.items():
        text = _whole_windows(windows, size)
        if text is None or hashlib.sha256(text.encode()).hexdigest() != sha:
            continue
        try:
            data = json.loads(text)
            query, rows = data["query"], data["rows"]
            path_text, query_digest = origin[len("table:") :].rsplit(":", 1)
            path = workspace_path(path_text)
        except (ValueError, KeyError, TypeError):
            continue
        if (
            path
            and Path(path).suffix.lower() == ".csv"
            and isinstance(query, dict)
            and query.get("sheet") == "Sheet1"
            and not query.get("filters")
            and not query.get("group_by")
            and not query.get("metrics")
            and _sha(data.get("input_sha256"))
            and isinstance(rows, list)
            and all(isinstance(row, dict) for row in rows)
            and type(data.get("matched_rows")) is int
            and data["matched_rows"] == len(rows)
            and hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:16]
            == query_digest
        ):
            complete.setdefault(path, set()).add(data["input_sha256"])
    return complete


def sufficient_paths(receipts, expected):
    """Require complete observed content of the fixture's original input version."""
    versions = complete_versions(receipts)
    return {
        path
        for path, sha in expected.get("input_hashes", {}).items()
        if sha in versions.get(path, set())
    }


def publication_checks(receipts, root, expected):
    """Counts and final-version readback are separate from answer correctness."""
    writes = {}
    for index, receipt in enumerate(receipts):
        if receipt.get("tool") in WRITE_TOOLS and receipt.get("status") == "returned":
            path = receipt_path(receipt)
            if path:
                writes.setdefault(path, []).append((index, receipt))
    counts = {path: len(items) for path, items in writes.items()}
    once = {path: counts.get(path, 0) == 1 for path in expected.get("single_publication_paths", [])}
    readback = None
    path = expected.get("readback")
    if path:
        readback = False
        if writes.get(path):
            index, receipt = writes[path][-1]
            value = receipt.get("value", {})
            try:
                final_hash = hashlib.sha256((root / path).read_bytes()).hexdigest()
            except OSError:
                final_hash = None
            readback = bool(
                final_hash
                and isinstance(value, dict)
                and workspace_path(value.get("path")) == path
                and value.get("sha256") == final_hash
                and final_hash in complete_versions(receipts[index + 1 :]).get(path, set())
            )
    return {
        "successful_writes_by_path": counts,
        "single_publication_checks": once,
        "final_version_readback": readback,
        "passed": all(once.values()) and readback is not False,
    }


def table_sum_observed(receipts, expected):
    """The table fixture permits the actual exact sum computation, not arbitrary queries."""
    for receipt in receipts:
        args, value = receipt.get("args", {}), receipt.get("value", {})
        if (
            receipt.get("tool") != "table_query"
            or receipt.get("status") != "returned"
            or workspace_path(args.get("path")) != "sales.csv"
            or not isinstance(value, dict)
            or value.get("input_sha256") != expected["input_hashes"].get("sales.csv")
            or args.get("filters")
            or args.get("group_by")
            or args.get("offset", 0) != 0
            or value.get("next_offset") is not None
            or value.get("rows_in_source") is not False
        ):
            continue
        metrics, rows = args.get("metrics"), value.get("rows")
        if not isinstance(metrics, list) or not isinstance(rows, list) or len(rows) != 1:
            continue
        for metric in metrics:
            if (
                isinstance(metric, dict)
                and metric.get("op") == "sum"
                and metric.get("column") == "amount"
                and isinstance(rows[0], dict)
                and str(rows[0].get(metric.get("as", "sum_amount"))) == str(expected["total"])
            ):
                return True
    return False


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


def accessed_before(receipts, index, path):
    """Early source access cannot be erased by a later correctly ordered read."""
    return any(
        receipt_path(r) == path or path in observed_paths([r]) or search_covers_forbidden(r, {path})
        for r in receipts[:index]
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
    parent_paths = sufficient_paths(result.get("_receipts", []), expected)
    if case == "release_audit":
        for answer in expected["worker_answers"]:
            matches = [
                w
                for w in workers
                if equal(w["_view"].get("value"), answer)
                and answer["source"] in sufficient_paths(w.get("_receipts", []), expected)
            ]
            if len(matches) != 1:
                return False
            if answer["project"] == expected["fallback_project"]:
                receipts = matches[0].get("_receipts", [])
                missing = [
                    i
                    for i, r in enumerate(receipts)
                    if r.get("status") == "raised"
                    and receipt_path(r) == expected["missing_primary"]
                    and (r.get("error") or {}).get("type") == "FileNotFoundError"
                ]
                fallback = [
                    i
                    for i, r in enumerate(receipts)
                    if r.get("status") == "returned"
                    and receipt_path(r) == answer["source"]
                    and r.get("tool") in READ_TOOLS
                ]
                if (
                    not missing
                    or not fallback
                    or min(missing) >= min(fallback)
                    or accessed_before(receipts, min(missing), answer["source"])
                    or answer["source"]
                    not in sufficient_paths(receipts[min(missing) + 1 :], expected)
                ):
                    return False
        return set(expected["required_reads"]) <= parent_paths
    if case == "multi":
        for path, count in zip(("left.json", "right.json"), expected["counts"], strict=True):
            matches = [
                w
                for w in workers
                if path_value_equal(w["_view"].get("value"), {"count": count, "path": path})
                and path in sufficient_paths(w.get("_receipts", []), expected)
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
            and first_path in sufficient_paths(w.get("_receipts", []), expected)
            and not w.get("depends_on")
        ),
        None,
    )
    second = next(
        (
            w
            for w in workers
            if equal(w["_view"].get("value"), second_answer)
            and second_path in sufficient_paths(w.get("_receipts", []), expected)
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
    paths = sufficient_paths(receipts, expected)
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
                provenance &= "optional.json" in paths and not accessed_before(
                    receipts, len(receipts), "evidence.json"
                )
            else:
                missing = [
                    i
                    for i, r in enumerate(receipts)
                    if r in optional
                    and r.get("status") == "raised"
                    and (r.get("error") or {}).get("type") == "FileNotFoundError"
                ]
                provenance &= bool(missing) and "evidence.json" in sufficient_paths(
                    receipts[min(missing) + 1 :] if missing else [], expected
                )
                provenance &= bool(missing) and not accessed_before(
                    receipts, min(missing) if missing else 0, "evidence.json"
                )
        elif case == "table":
            checks[case] = equal(value, expected["total"]) or value == str(expected["total"])
            provenance &= "sales.csv" in paths or table_sum_observed(receipts, expected)
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
            provenance &= "evidence.json" in paths
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
        # Editing must be based on the old version observed before the first
        # successful mutation, not merely a later read of the final output.
        for path in expected.get("allowed_changes", []):
            first_write = next(
                (
                    i
                    for i, r in enumerate(receipts)
                    if r.get("tool") in WRITE_TOOLS
                    and r.get("status") == "returned"
                    and receipt_path(r) == path
                ),
                None,
            )
            provenance &= first_write is not None and path in sufficient_paths(
                receipts[:first_write], expected
            )
    publication = publication_checks(receipts, root, expected)
    provenance &= publication["passed"]
    required_before_publish = (
        expected.get("required_reads", [])
        if expected.get("readback")
        else ["evidence.json"]
        if case in {"write", "report"}
        else []
    )
    if required_before_publish:
        first_write = next(
            (
                i
                for i, r in enumerate(receipts)
                if r.get("tool") in WRITE_TOOLS and r.get("status") == "returned"
            ),
            None,
        )
        provenance &= first_write is not None and set(required_before_publish) <= sufficient_paths(
            receipts[:first_write], expected
        )
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
        "evidence_scope": "Observed content/version coverage, ordering and fixture checks; not proof of understanding, semantic use or causal correctness",
        "coverage_limitations": [
            "Byte and line windows are evaluated independently; mixed partial representations may be conservatively rejected",
            "Document extraction fidelity is trusted; full content observation does not establish comprehension",
            "Full table-row coverage supports single-table CSV; a selected XLSX worksheet is not treated as a complete workbook",
        ],
        "publication_checks": publication,
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
    if getattr(args, "reopen_after_write", False) and not getattr(args, "resume_attempts", 0):
        raise ValueError("reopen-after-write requires at least one resume attempt")
    output, repository = args.output.resolve(), Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        raise ValueError("Evaluation output must be outside the repository")
    return models, cases


def evaluate(args, key):
    models, cases = validate_options(args)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    provenance = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": source_provenance(),
        "models": models,
        "cases": cases,
        "rounds": args.rounds,
        "seed": getattr(args, "seed", 20261001),
        "resume_attempts": getattr(args, "resume_attempts", 0),
        "reopen_after_write": getattr(args, "reopen_after_write", False),
        "pass_definition": "runtime completion AND independently correct task output AND evidence/mutation/publication integrity; research requires human review",
    }
    atomic_json(output / "provenance.json", provenance)
    rows = []
    unavailable_models = set()
    access_rejected = False
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
        if access_rejected or model in unavailable_models:
            rows.append(
                {
                    "model": model,
                    "case": case,
                    "trial": repeat + 1,
                    "fixture_seed": fixture_seed,
                    "status": "not_run",
                    "seconds": 0,
                    "reason": (
                        "Earlier authentication, balance or model-access rejection; no further requests"
                        if access_rejected
                        else "Earlier model transport failure; fix endpoint before retrying"
                    ),
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
        provider = profile.setdefault("provider", {})
        provider.update(
            model=model,
            base_url=args.base_url,
            api_key_env=None,
            allow_insecure_http=getattr(args, "allow_insecure_http", False),
            max_tokens_parameter="max_tokens",
        )
        provider.setdefault("timeout", 30)
        provider.setdefault("total_timeout", 120)
        profile.setdefault("compiler", {}).setdefault("compilation_timeout", 120)
        # Operational bounds prevent runaway calls; explicit profile limits/None are preserved.
        profile.setdefault("general", {}).update(allow_commands=False)
        profile["general"].setdefault("subagents", {})["enabled"] = case in COLLABORATIVE
        resolved_limits = apply_evaluation_limits(profile)
        events, attempts = [], []
        monitor = AccessFailureMonitor(
            events, pause_after_write=getattr(args, "reopen_after_write", False)
        )
        recovery = None
        configuration = configuration_view(profile)
        start = time.monotonic()
        result, app = {}, None
        phase = "initialize"

        try:
            app = GeneralAgent(
                session_dir=case_dir / "session",
                workspace=root,
                profile=profile,
                session_key=key,
                on_event=monitor,
            )
            monitor.app = app
            configuration = configuration_view(app.profile)
            for attempt in range(getattr(args, "resume_attempts", 0) + 1):
                phase = "run"
                result = {}
                result = app.run(TASKS[case]) if attempt == 0 else app.resume()
                # Legacy protocols omit application failure metadata. Derive only sanitized advice.
                result["failure"] = result.get("failure") or failure_info(
                    result["status"], result.get("reason", "")
                )
                if monitor.status is not None:
                    result["failure"] = failure_info(
                        "needs_program", f"model HTTP {monitor.status}"
                    )
                phase = "collect_evidence"
                collect_evidence(app, result)
                phase = "assess"
                grade = assess(case, result, root, events, expected)
                if monitor.status is not None:
                    grade["passed"] = False
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
                if monitor.write_paused and recovery is None and monitor.status is None:
                    # Operational interruption only: no oracle is sent to the model.
                    old_receipts = result["_receipts"]
                    old_budget = result["budget"]
                    app.close()
                    if monitor.status is not None:
                        break  # A worker may report access denial while close joins it.
                    app = GeneralAgent(
                        session_dir=case_dir / "session",
                        workspace=root,
                        session_key=key,
                        on_event=monitor,
                    )
                    monitor.app = app
                    restored = read_receipts(app.agent.current_trace_path)
                    recovery = {
                        "receipts_preserved": restored == old_receipts,
                        "budget_counters_preserved": all(
                            app.agent.status()["budget"].get(k) == old_budget.get(k)
                            for k in COUNTERS
                        ),
                        "before_receipt_count": len(old_receipts),
                    }
                    if result["status"] in {
                        "paused",
                        "needs_program",
                        "stalled",
                        "incomplete",
                    } and attempt < getattr(args, "resume_attempts", 0):
                        continue
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
        if monitor.status is not None:
            row["failure"] = failure_info("needs_program", f"model HTTP {monitor.status}")
        if (row.get("failure") or {}).get("code") == "model_transport" and not getattr(
            args, "continue_on_transport_error", False
        ):
            unavailable_models.add(model)
        if (row.get("failure") or {}).get("http_status") in {401, 402, 403, 404}:
            access_rejected = True
        row["resolved_limits"] = resolved_limits
        row["configuration"] = configuration
        if recovery is not None:
            row["reopen_recovery"] = recovery
        row["mechanism_observations"] = mechanism_observations(events)
        row["compilation_telemetry"] = monitor.telemetry()
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
            "provenance": "provenance.json",
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
    parser.add_argument(
        "--reopen-after-write",
        action="store_true",
        help="Pause after the first successful parent publication, close and reopen the saved session; requires a resume allowance",
    )
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
