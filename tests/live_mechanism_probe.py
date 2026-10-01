"""Opt-in dual-control probe with explicit or task-level natural elicitation.

The real model authors every program, forecast and witness. Local tools simulate
one irreversible dispatch commitment. The compiler teaches the mechanism in both
modes; fixture success does not establish spontaneous synthesis or causal benefit.
"""

from __future__ import annotations

import argparse
import json
import os
import random
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
from flora.support.values import canonical_json, clone
from tests.live_reliability_probe import (
    AccessFailureMonitor,
    configuration_view,
    mechanism_observations,
    source_provenance,
)

SCOPE = (
    "Explicitly elicited model-authored alternatives and diagnostic; local simulated "
    "one-shot commitment. No host-authored candidate/forecast/consumer, no spontaneous "
    "synthesis claim, and no causal-benefit claim."
)
NATURAL_SCOPE = (
    "Task-level unelicited mechanism use: the user task does not request alternatives, "
    "forecasts or diagnostics, but the compiler still teaches the mechanism. Local "
    "simulated one-shot commitment; no host-authored candidate/forecast/consumer, "
    "no spontaneous-synthesis claim, and no causal-benefit claim."
)
LEAD_MINUTES = 17


def elicitation_metadata(elicitation):
    if elicitation not in ("explicit", "natural"):
        raise ValueError("elicitation must be explicit or natural")
    return {
        "elicitation": elicitation,
        "task_level_mechanism_elicited": elicitation == "explicit",
        "compiler_mechanism_instructions": True,
        "scope": SCOPE if elicitation == "explicit" else NATURAL_SCOPE,
    }


class DispatchWorld:
    """Tools enforce shape and one-shot effects, never the correct decision."""

    def __init__(self, cutoff):
        self.cutoff = cutoff
        self.committed = None
        self.calls = []

    def read_cutoff(self):
        self.calls.append({"tool": "read_cutoff", "args": {}})
        return {"cutoff_minutes": self.cutoff}

    def commit_dispatch(self, departure_minutes):
        self.calls.append(
            {"tool": "commit_dispatch", "args": {"departure_minutes": departure_minutes}}
        )
        if self.committed is not None:
            raise ValueError("Dispatch already committed; this simulation cannot undo it")
        # Wrong decisions are committed too. The tool must not supply an oracle
        # rejection that steers the model toward the expected answer.
        self.committed = departure_minutes
        return {"committed": True, "departure_minutes": departure_minutes}

    def specs(self):
        return [
            ToolSpec(
                "read_cutoff",
                self.read_cutoff,
                "Observe the current cutoff. Returns {cutoff_minutes: integer}. This is the "
                "independent live state; source notes may be stale. Does not commit anything.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolSpec(
                "commit_dispatch",
                self.commit_dispatch,
                "Irreversibly commit one departure in this LOCAL SIMULATION. A second call "
                "raises; there is no undo. Returns {committed:true,departure_minutes:integer}. "
                "The tool accepts any time in bounds; it does not judge correctness.",
                {
                    "type": "object",
                    "properties": {
                        "departure_minutes": {"type": "integer", "minimum": 0, "maximum": 1439}
                    },
                    "required": ["departure_minutes"],
                    "additionalProperties": False,
                },
            ),
        ]


def task_text(cutoffs, elicitation="explicit"):
    elicitation_metadata(elicitation)
    request = (
        f"Dispatch one shipment exactly {LEAD_MINUTES} minutes before the CURRENT cutoff. "
        f"Two credible operations records conflict: record A says {cutoffs[0]} minutes "
        f"after midnight; record B says {cutoffs[1]}. Neither record has a trustworthy "
        "freshness advantage. Independently observe the current cutoff before committing; "
        "do not guess or commit both. Return only the actual commitment receipt object. "
    )
    exercise = (
        "This is an explicitly elicited dual-control exercise: retain the two genuinely "
        "different source-based commitment alternatives as normal candidates, and author "
        "an informative diagnostic that observes the independent state before any commitment. "
        "Author your own forecasts, hypothetical witnesses and a consumer that uses the "
        "ACTUAL observation to commit the appropriate departure. Witnesses are hypothetical, "
        "not observed facts. Do not manufacture unrelated alternatives or claim that an "
        "ordinary conditional branch alone proves diagnostic insertion. "
    )
    return (
        request
        + (exercise if elicitation == "explicit" else "")
        + ("All tools affect only this local simulation.")
    )


def assess(world, cutoffs, result, receipts, events, selections, elicitation="explicit"):
    """External post-run checks; this output is never fed to the compiler."""
    expected = world.cutoff - LEAD_MINUTES
    expected_value = {"committed": True, "departure_minutes": expected}
    ordered = (
        len(receipts) == 2
        and [r.get("tool") for r in receipts] == ["read_cutoff", "commit_dispatch"]
        and all(r.get("status") == "returned" for r in receipts)
        and receipts[0].get("args") == {}
        and receipts[0].get("value") == {"cutoff_minutes": world.cutoff}
        and receipts[1].get("args") == {"departure_minutes": expected}
        and receipts[1].get("value") == expected_value
    )
    task_passed = (
        result.get("status") == "completed"
        and canonical_json(result.get("value")) == canonical_json(expected_value)
        and type(world.committed) is int
        and world.committed == expected
        and [c["tool"] for c in world.calls] == ["read_cutoff", "commit_dispatch"]
        and ordered
    )
    expected_actions = {
        canonical_json(
            {"tool": "commit_dispatch", "args": {"departure_minutes": cutoff - LEAD_MINUTES}}
        )
        for cutoff in cutoffs
    }
    inserted = False
    for selection in selections:
        if not selection.get("diagnostic") or selection.get("tool") != "read_cutoff":
            continue
        normal = selection["normal_frontiers"]
        if len(normal) != 2 or {canonical_json(n["request"]) for n in normal} != expected_actions:
            continue
        ident, epoch = selection["candidate"], selection["epoch"]
        positive = any(
            e.get("kind") == "diagnostic_evaluated"
            and e.get("diagnostic") == ident
            and e.get("epoch") == epoch
            and type(e.get("report", {}).get("score")) in (int, float)
            and e["report"]["score"] > 0
            for e in events
        )
        feedback = any(
            e.get("kind") == "forecast_observation"
            and e.get("diagnostic") == ident
            and receipts
            and e.get("report", {}).get("event_id") == receipts[0].get("event_id")
            and e["report"].get("previous_hash") == receipts[0].get("previous_hash")
            and e["report"].get("request") == {"tool": "read_cutoff", "args": {}}
            and {v.get("candidate_id") for v in e["report"].get("verdicts", [])}
            == {n["id"] for n in normal}
            and {v.get("verdict") for v in e["report"].get("verdicts", [])} == {"PASS", "FAIL"}
            for e in events
        )
        inserted |= positive and feedback
    return {
        "task_passed": bool(task_passed),
        "mechanism_passed": bool(task_passed and inserted),
        "diagnostic_evidence_linked": bool(inserted),
        **elicitation_metadata(elicitation),
        "spontaneous_synthesis_verified": False,
        "causal_benefit_verified": False,
    }


def action_trajectory(receipts, selections):
    """Link observed selections to actual receipts; never execute or infer an action."""
    trajectory = []
    for receipt in receipts:
        matching = [
            selection
            for selection in selections
            if selection.get("epoch") == receipt.get("event_id")
            and selection.get("trace_digest") == receipt.get("previous_hash")
            and selection.get("tool") == receipt.get("tool")
        ]
        selection = matching[0] if len(matching) == 1 else None
        trajectory.append(
            {
                "event_id": receipt.get("event_id"),
                "previous_hash": receipt.get("previous_hash"),
                "request": {"tool": receipt.get("tool"), "args": clone(receipt.get("args"))},
                "status": receipt.get("status"),
                **{key: clone(receipt[key]) for key in ("value", "error") if key in receipt},
                "selection_linked": selection is not None,
                "selection": None
                if selection is None
                else {
                    key: clone(selection.get(key))
                    for key in ("candidate", "diagnostic", "shared_with", "normal_frontiers")
                },
            }
        )
    return trajectory


def run_case(
    provider, compiler_options, cutoffs, cutoff, directory, *, key="", elicitation="explicit"
):
    """No seed bundle: the compiler/model authors the initial and later programs."""
    task = task_text(cutoffs, elicitation)
    world = DispatchWorld(cutoff)
    events, selections, bundles = [], [], []
    monitor = AccessFailureMonitor(events)
    (directory / "dialogue").mkdir(mode=0o700)
    store = ObservationStore(directory / "dialogue")
    dialogue = Dialogue(store, monitor, secrets=(key,))
    provider.on_event = dialogue.event
    compiler = LLMCompiler(provider, **compiler_options)
    compiler.transport_retries = 1
    compiler.on_event = dialogue.event

    def observe(event):
        monitor(event)
        if event.get("kind") == "bundle_installed":
            bundles.append(
                {
                    "epoch": event["epoch"],
                    "programs": [
                        {"id": c.id, "program": clone(c.machine.program), "inputs": clone(c.inputs)}
                        for c in runtime.candidates.values()
                        if c.kind == "normal"
                    ],
                    "diagnostics": clone(list(runtime.diagnostics.values())),
                }
            )
        elif event.get("kind") == "action_selected":
            # Observe machines already suspended by the real runtime. Do not
            # execute hypothetical code here or manufacture scheduler rows.
            selections.append(
                {
                    **event,
                    "trace_digest": runtime.trace.digest,
                    "normal_frontiers": [
                        {"id": c.id, "request": clone(c.machine.pending["request"])}
                        for c in runtime.candidates.values()
                        if c.kind == "normal"
                        and c.status == "ACTIVE"
                        and c.machine.pending is not None
                        and c.anchor_epoch == runtime.trace.epoch
                        and c.anchor_digest == runtime.trace.digest
                    ],
                }
            )

    runtime = Runtime(
        ToolRegistry(dialogue.tools(world.specs())),
        compiler=compiler,
        on_event=observe,
        config=RuntimeConfig(max_compile_cycles=3, max_steps=40, max_diagnostic_calls=1),
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
    started = time.monotonic()
    result, exception = {}, None
    try:
        result = runtime.run(task).to_dict()
    except Exception as exc:
        exception = {"type": type(exc).__name__, "message": dialogue.clean(str(exc))}
    finally:
        dialogue.close()
        store.close()
        provider.on_event = None
    receipts = clone(runtime.trace.records)
    grade = assess(world, cutoffs, result, receipts, events, selections, elicitation)
    if exception is not None or monitor.status is not None:
        grade["task_passed"] = grade["mechanism_passed"] = False
    return {
        "task": task,
        **elicitation_metadata(elicitation),
        "source_cutoffs": list(cutoffs),
        "actual_cutoff_after_run": cutoff,
        "result": result,
        "exception": exception,
        "access_rejected": monitor.status,
        "grade": grade,
        "seconds": round(time.monotonic() - started, 3),
        "receipts": receipts,
        "host_calls": clone(world.calls),
        "model_bundles": bundles,
        "selections": selections,
        "action_trajectory": action_trajectory(receipts, selections),
        "mechanism_observations": mechanism_observations(events),
        "events": events,
    }


def _redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    if isinstance(value, dict):
        return {_redact(k, key): _redact(v, key) for k, v in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", required=True, choices=("deepseek-flash", "deepseek-v4-pro"))
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--seed", type=int, default=20261001)
    parser.add_argument("--elicitation", choices=("explicit", "natural"), default="explicit")
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
            "seed": args.seed,
            "rounds": args.rounds,
            "configuration": configuration_view(
                {"provider": provider_options, "compiler": compiler_options}
            ),
            **elicitation_metadata(args.elicitation),
            "planned_cases": args.rounds * 2,
        },
    )
    rng, rows, blocked = random.Random(args.seed), [], False
    try:
        for repeat in range(args.rounds):
            cutoffs = sorted(rng.sample(range(300, 1200, 5), 2))
            states = list(cutoffs)
            rng.shuffle(states)
            for variant, cutoff in enumerate(states):
                if blocked:
                    rows.append(
                        {
                            "round": repeat + 1,
                            "variant": variant + 1,
                            "status": "not_run",
                            **elicitation_metadata(args.elicitation),
                        }
                    )
                    save(output / "results.json", rows)
                    continue
                directory = output / f"r{repeat + 1}-v{variant + 1}"
                directory.mkdir(mode=0o700)
                provider = OpenAICompatibleProvider(**provider_options)
                provider.set_session_key(key)

                def deadline(signum, frame):
                    raise TimeoutError("mechanism probe operator deadline (660s)")

                previous = signal.signal(signal.SIGALRM, deadline)
                signal.alarm(660)
                try:
                    row = run_case(
                        provider,
                        compiler_options,
                        cutoffs,
                        cutoff,
                        directory,
                        key=key,
                        elicitation=args.elicitation,
                    )
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, previous)
                    provider.set_session_key(None)
                row.update(round=repeat + 1, variant=variant + 1)
                blocked = row["access_rejected"] is not None
                rows.append(row)
                save(directory / "result.json", row)
                save(output / "results.json", rows)
                print(
                    json.dumps({k: row[k] for k in ("round", "variant", "grade", "seconds")}),
                    flush=True,
                )
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
