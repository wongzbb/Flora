"""Elicited legacy mixed-history repair probe, not spontaneous or E2E synthesis.

Only the intentionally incompatible legacy seed is host-authored. The real model
must write the replacement, pure migration and EXTEND declaration. Grading runs
only after execution and is never available as a tool or compiler input.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from flora.engine.budget import Budget, BudgetLimits
from flora.engine.runtime import Runtime, RuntimeConfig
from flora.general.agent import read_profile
from flora.general.observability import Dialogue
from flora.general.storage import ObservationStore
from flora.integrations.providers import OpenAICompatibleProvider
from flora.integrations.tools import ToolRegistry, ToolSpec
from flora.language.compiler import LLMCompiler
from flora.support.values import clone, digest
from tests.helpers import block, bundle
from tests.live_mechanism_probe import _redact
from tests.live_reliability_probe import (
    AccessFailureMonitor,
    configuration_view,
    mechanism_observations,
    source_provenance,
)

SCOPE = (
    "Explicitly elicited model-authored EXTEND after a host-authored legacy consumer fault. "
    "Actual two-page observations and mixed historical/current gates. Not end-to-end "
    "generation, spontaneous revision, general correctness or causal-benefit evidence."
)


class PageWorld:
    def __init__(self, sizes=(2, 3)):
        self.pages = [
            {"items": [f"a{i}" for i in range(sizes[0])], "next": 1},
            {"records": [f"b{i}" for i in range(sizes[1])], "next": None},
        ]
        self.calls = []

    def read_page(self, page, acknowledged_count):
        # Deliberately no semantic check of acknowledgement and no repair hint.
        self.calls.append(
            {
                "tool": "read_page",
                "args": {
                    "page": page,
                    "acknowledged_count": acknowledged_count,
                },
            }
        )
        if type(page) is not int or page not in (0, 1):
            raise ValueError("Page is outside this two-page fixture")
        return clone(self.pages[page])

    def specs(self):
        return [
            ToolSpec(
                "read_page",
                self.read_page,
                "Read one fixture page without modification. Payload has a list under items "
                "or records and next (page index or null). acknowledged_count is logged, not "
                "validated against any answer. Returns the actual page payload.",
                {
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "minimum": 0, "maximum": 1},
                        "acknowledged_count": {"type": "integer", "minimum": 0},
                    },
                    "required": ["page", "acknowledged_count"],
                    "additionalProperties": False,
                },
            )
        ]


def legacy_seed():
    """Old items-only consumer; actually reaches page 1 before its shape fault."""

    def v(name):
        return {"var": name}

    def op(name, dest, *args):
        return {"op": name, "dest": dest, "args": list(args)}

    def effect(page, count):
        return {
            "op": "effect",
            "tool": "read_page",
            "args": {"page": page, "acknowledged_count": count},
            "bind": "reply",
            "capture": {"count": count},
            "resume": "consume",
        }

    return bundle(
        {
            "version": 1,
            "entry": "main",
            "blocks": {
                "main": block(term=effect(0, 0)),
                "consume": block(
                    ["reply", "count"],
                    [
                        op("get", "payload", v("reply"), "value"),
                        op("get", "items", v("payload"), "items"),
                        op("length", "n", v("items")),
                        op("add", "total", v("count"), v("n")),
                        op("get", "next", v("payload"), "next"),
                        op("eq", "done", v("next"), None),
                    ],
                    {
                        "op": "branch",
                        "condition": v("done"),
                        "yes": "done",
                        "no": "more",
                        "args": {"total": v("total"), "next": v("next")},
                    },
                ),
                "done": block(["total", "next"], term={"op": "return", "value": v("total")}),
                "more": block(["total", "next"], term=effect(v("next"), v("total"))),
            },
        }
    )


def task_text():
    return (
        "Count the records across both pages and return only the total as a JSON number. "
        "Before requesting a next page, acknowledged_count must equal the cumulative number "
        "already consumed. Page payloads can use items or records; follow next until null. "
        "This explicitly elicited legacy-repair exercise has already run an old items-only "
        "consumer against both actual pages, then faulted on the changed page shape. "
        "Do not repeat either observation. Author an EXTEND revision of the existing main "
        "candidate, your own pure migration from actual checkpoint/current inputs, and a "
        "complete replacement consumer that handles both shapes. Include main as the incoming "
        "normal candidate with the same program as its revision so accepted state is activated. "
        "The replacement must preserve the already-defined historical next-page request, "
        "including its acknowledgement, and extend the formerly faulting prefix to a defined "
        "result. Use runtime syntax and revision_state; do not claim ordinary replacement or "
        "a constant answer is proof of checked revision. The host supplies no repair program."
    )


def assess(world, result, receipts, events, activations, seed_result):
    expected = sum(len(p.get("items", p.get("records", []))) for p in world.pages)
    expected_calls = [
        {"tool": "read_page", "args": {"page": 0, "acknowledged_count": 0}},
        {
            "tool": "read_page",
            "args": {"page": 1, "acknowledged_count": len(world.pages[0]["items"])},
        },
    ]
    task_passed = (
        result.get("status") == "completed"
        and type(result.get("value")) is int
        and result["value"] == expected
        and world.calls == expected_calls
        and len(receipts) == 2
        and all(
            r.get("status") == "returned"
            and r.get("value") == p
            and {"tool": r.get("tool"), "args": r.get("args")} == c
            for r, p, c in zip(receipts, world.pages, expected_calls)
        )
    )
    mixed = []
    for index, event in enumerate(events):
        results = event.get("results", [])
        if (
            event.get("kind") != "revision_checked"
            or event.get("mode") != "EXTEND"
            or not event.get("accepted")
            or len(results) < 2
            or any(r.get("verdict") != "PASS" for r in results)
            or {r.get("relation") for r in results} != {"SAME_BOUNDARY", "DEFINED_PREFIX"}
            or event.get("current_check", {}).get("verdict") != "PASS"
        ):
            continue
        if any(
            a["event_index"] > index
            and a["target"] == event.get("target")
            and a["revision_id"] == event.get("id")
            and a["program_matches_revision"]
            and a["final_program_matches"]
            and a["program_digest"]
            == event["current_check"].get("witness", {}).get("program_digest")
            and any(
                e.get("kind") == "final_return" and e.get("candidate") == a["target"]
                for e in events[a["event_index"] + 1 :]
            )
            for a in activations
        ):
            mixed.append(event["id"])
    seeded_fault = seed_result.get("status") == "needs_program" and any(
        e.get("kind") == "local_execution" and e.get("status") == "fault"
        for e in seed_result.get("reports", [])
    )
    return {
        "task_passed": bool(task_passed),
        "mechanism_passed": bool(task_passed and seeded_fault and mixed),
        "linked_mixed_history_revisions": mixed,
        "scope": SCOPE,
        "spontaneous_synthesis_verified": False,
    }


def run_case(provider, compiler_options, sizes, directory, *, key="", resume_attempts=0):
    if type(resume_attempts) is not int or not 0 <= resume_attempts <= 2:
        raise ValueError("resume_attempts must be an integer from 0 to 2")
    world = PageWorld(sizes)
    events, activations, proposals = [], [], []
    monitor = AccessFailureMonitor(events)
    (directory / "dialogue").mkdir(mode=0o700)
    store = ObservationStore(directory / "dialogue")
    dialogue = Dialogue(store, monitor, secrets=(key,))
    provider.on_event = dialogue.event
    compiler = LLMCompiler(provider, **compiler_options)
    compiler.transport_retries = 1
    compiler.on_event = dialogue.event
    original_compile = compiler.compile

    def compile_checked(context):
        if monitor.status is not None:
            raise RuntimeError("Model access denied; later compilation blocked")
        proposed = original_compile(context)
        proposals.append(clone(proposed))
        return proposed

    compiler.compile = compile_checked

    def observe(event):
        monitor(event)
        if event.get("kind") == "bundle_installed" and proposals:
            for revision in proposals[-1].get("revisions", []):
                target = revision["target_candidate"]
                candidate = runtime.candidates.get(target)
                activations.append(
                    {
                        "event_index": len(events) - 1,
                        "target": target,
                        "revision_id": revision["id"],
                        "program_digest": digest(revision["program"]),
                        "program_matches_revision": candidate is not None
                        and digest(candidate.machine.program) == digest(revision["program"]),
                    }
                )

    runtime = Runtime(
        ToolRegistry(dialogue.tools(world.specs())),
        compiler=compiler,
        on_event=observe,
        config=RuntimeConfig(max_compile_cycles=3, max_steps=40),
        budget=Budget(
            BudgetLimits(
                max_tool_calls=4,
                max_model_calls=4,
                max_input_tokens=200000,
                max_output_tokens=96000,
                max_wall_seconds=600,
            )
        ),
    )
    # Bind normal Runtime budget accounting before temporarily disabling compilation.
    runtime.compiler = None
    seed = legacy_seed()
    result, seed_result, exception = {}, {}, None
    attempts = []
    started = time.monotonic()
    try:
        seed_result = runtime.run(task_text(), bundle=seed).to_dict()
        if seed_result["status"] != "needs_program" or len(world.calls) != 2:
            raise RuntimeError("Legacy fixture did not reach its required actual two-page fault")
        runtime.compiler = compiler
        for _ in range(resume_attempts + 1):
            if monitor.status is not None:
                break
            event_start = len(events)
            try:
                result = runtime.run(task_text()).to_dict()
            except Exception as exc:
                result = {
                    "status": "exception",
                    "reason": dialogue.clean(str(exc)),
                    "budget": clone(runtime.budget.to_dict()),
                }
                attempts.append(clone(result))
                raise
            attempts.append(clone(result))
            # Only this actual gate refusal is resumable. Do not consult the
            # task oracle or reset Runtime history, candidates or budget.
            rejected = any(
                event.get("kind") == "revision_checked" and event.get("accepted") is False
                for event in events[event_start:]
            )
            if not (
                monitor.status is None
                and result.get("status") == "needs_program"
                and result.get("reason")
                == "All incoming programs failed their declared revision gates"
                and rejected
            ):
                break
    except Exception as exc:
        exception = {"type": type(exc).__name__, "message": dialogue.clean(str(exc))}
    finally:
        dialogue.close()
        store.close()
        provider.on_event = None
    for activation in activations:
        final_candidate = runtime.candidates.get(activation["target"])
        activation["final_program_matches"] = final_candidate is not None and (
            digest(final_candidate.machine.program) == activation["program_digest"]
        )
    receipts = clone(runtime.trace.records)
    grade = assess(world, result, receipts, events, activations, seed_result)
    if exception is not None or monitor.status is not None:
        grade["task_passed"] = grade["mechanism_passed"] = False
    return {
        "task": task_text(),
        "seed": seed,
        "seed_result": seed_result,
        "result": result,
        "attempts": attempts,
        "resume_attempts": resume_attempts,
        "exception": exception,
        "access_rejected": monitor.status,
        "grade": grade,
        "seconds": round(time.monotonic() - started, 3),
        "receipts": receipts,
        "host_calls": clone(world.calls),
        "model_bundles": proposals,
        "activations": activations,
        "events": events,
        "mechanism_observations": mechanism_observations(events),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", required=True, choices=("deepseek-flash", "deepseek-v4-pro"))
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--resume-attempts", type=int, choices=range(3), default=0)
    args = parser.parse_args()
    if args.base_url.rstrip("/") != "https://api.deepseek.com":
        parser.error("Only the explicitly authorized official HTTPS endpoint is allowed")
    output = args.output.resolve()
    repository = Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        parser.error("Evaluation output must be outside the repository")
    profile = read_profile(args.profile)
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error("The explicitly named API-key environment variable is empty")
    provider_options = {
        **profile.get("provider", {}),
        "base_url": args.base_url,
        "model": args.model,
        "api_key_env": None,
        "allow_insecure_http": False,
        "stream": True,
    }
    compiler_options = {
        **profile.get("compiler", {}),
        "syntax": "block-list-v2",
        "prompt_style": "compact-v2",
    }
    output.mkdir(parents=True, exist_ok=False, mode=0o700)

    def save(path, value):
        path.write_text(json.dumps(_redact(value, key), ensure_ascii=False, indent=2) + "\n")

    save(
        output / "provenance.json",
        {
            "source": source_provenance(),
            "model": args.model,
            "rounds": args.rounds,
            "resume_attempts": args.resume_attempts,
            "configuration": configuration_view(
                {"provider": provider_options, "compiler": compiler_options}
            ),
            "scope": SCOPE,
            "planned_cases": args.rounds,
        },
    )
    rows, blocked = [], False
    try:
        for repeat in range(args.rounds):
            if blocked:
                rows.append({"round": repeat + 1, "status": "not_run"})
                save(output / "results.json", rows)
                continue
            directory = output / f"r{repeat + 1}"
            directory.mkdir(mode=0o700)
            provider = OpenAICompatibleProvider(**provider_options)
            provider.set_session_key(key)

            def deadline(signum, frame):
                raise TimeoutError("revision probe operator deadline (660s)")

            previous = signal.signal(signal.SIGALRM, deadline)
            signal.alarm(660)
            try:
                row = run_case(
                    provider,
                    compiler_options,
                    (repeat + 2, repeat + 3),
                    directory,
                    key=key,
                    resume_attempts=args.resume_attempts,
                )
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
                provider.set_session_key(None)
            row["round"] = repeat + 1
            blocked = row["access_rejected"] is not None
            rows.append(row)
            save(directory / "result.json", row)
            save(output / "results.json", rows)
            print(json.dumps({k: row[k] for k in ("round", "grade", "seconds")}), flush=True)
    finally:
        leaked = any(key.encode() in p.read_bytes() for p in output.rglob("*") if p.is_file())
        print(json.dumps({"phase": "credential_scan", "saved_secret": leaked}))
        if leaked:
            raise SystemExit(2)
    return (
        3 if blocked else 0 if all(r.get("grade", {}).get("mechanism_passed") for r in rows) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
