# SPDX-License-Identifier: Apache-2.0
"""Local deterministic checks of the downloaded Flora revision; no external services."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from flora.agent.api import Agent
from flora.checks.contracts import (
    ContextCheck,
    Verdict,
    check_revision,
    evaluate_predicate,
    fit_guard,
)
from flora.checks.reuse import ReuseLibrary
from flora.engine.budget import Budget, BudgetLimits
from flora.engine.replay import replay
from flora.engine.runtime import Runtime, RuntimeConfig
from flora.examples import (
    CalendarWorld,
    block,
    calendar_bundle,
    effect,
    move_program,
    op,
    pagination_program,
    run_demo,
    var,
)
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.integrations.tools import ToolRegistry
from flora.language.compiler import LLMCompiler, ScriptedCompiler, validate_bundle
from flora.language.ir import parse_program
from flora.language.vm import new_machine, run_until_boundary
from flora.state.trace import GENESIS, MemoryTrace, SQLiteTrace
from flora.support.errors import BudgetExceeded, StaleAnchor, TraceIntegrityError, ValidationError

ROOT = Path(__file__).resolve().parents[1]
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def expect_error(kind, fn):
    try:
        fn()
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


def pure(value, params=(), ops=()):
    return {
        "version": 1,
        "entry": "main",
        "blocks": {"main": block(list(params), list(ops), {"op": "return", "value": value})},
    }


def bundle(program, epoch=0, trace_digest=GENESIS, inputs=None):
    return {
        "programs": [{"id": "main", "program": program, "inputs": inputs or {}}],
        "incumbent": "main",
        "diagnostics": [],
        "expected_epoch": epoch,
        "expected_digest": trace_digest,
    }


def effect_program(tool_name):
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block([], [], effect(tool_name, {})),
            "done": block(
                ["reply"],
                [op("get", "value", var("reply"), "value")],
                {"op": "return", "value": var("value")},
            ),
        },
    }


@check
def source_syntax():
    paths = sorted((ROOT / "src").rglob("*.py"))
    for path in paths:
        ast.parse(path.read_text(), filename=str(path))
    return {"python_files": len(paths)}


@check
def published_json_examples():
    checked = []
    for path in sorted((ROOT / "examples").glob("*.json")):
        data = json.loads(path.read_text())
        (validate_bundle if ".bundle." in path.name else parse_program)(data)
        checked.append(path.name)
    return {"validated": checked}


@check
def calendar_diagnostic():
    data = run_demo("calendar")
    assert data["result"]["status"] == "completed"
    assert data["host_observation"]["events"]["A"] == 930
    assert data["result"]["budget"]["tool_calls"] == 2
    assert [x["tool"] for x in data["host_observation"]["calls"]] == ["read_event", "move_event"]
    return {"A": 930, "tools": 2, "external_model_calls": 0}


@check
def completed_is_not_correctness():
    data = run_demo("calendar", config=RuntimeConfig(enable_diagnostics=False))
    assert data["result"]["status"] == "completed"
    assert data["host_observation"]["events"]["A"] == 870
    assert data["host_observation"]["events"]["B"] == 900
    assert data["result"]["budget"]["tool_calls"] == 1
    assert any(
        r["kind"] == "consumer_check" and r["result"]["verdict"] == "PASS"
        for r in data["result"]["reports"]
    )
    return {
        "status": "completed",
        "local_contract": "PASS",
        "actual_A": 870,
        "task_required_A": 930,
    }


@check
def shared_effect_pagination():
    data = run_demo("pagination")
    assert data["result"]["value"] == ["a", "b", "c"]
    assert data["host_observation"]["cursor"] == 2
    rows = [r for r in data["result"]["reports"] if r["kind"] == "action_selected"]
    assert set(rows[0]["shared_with"]) == {"items_parser", "data_parser"}
    assert data["result"]["budget"]["tool_calls"] == 2
    return {
        "values": ["a", "b", "c"],
        "cursor": 2,
        "first_request_shared_by": rows[0]["shared_with"],
    }


@check
def vm_pauses_without_dispatch():
    world = CalendarWorld()
    machine = new_machine(move_program(930))
    before = machine.to_dict()
    boundary = run_until_boundary(machine)
    assert boundary.kind == "effect" and machine.to_dict() == before
    assert world.calls == []
    runtime = Runtime(world.registry())
    expect_error(
        ValidationError,
        lambda: runtime.executor.execute(
            boundary.request, epoch=0, trace_digest=GENESIS, mode="hypothetical"
        ),
    )
    assert world.calls == []
    return {
        "boundary": "effect",
        "caller_state_unchanged": True,
        "hypothetical_dispatch_rejected": True,
    }


@check
def invalid_capability_and_stale_anchor():
    world = CalendarWorld()
    expect_error(ValidationError, lambda: parse_program(move_program(930), []))
    runtime = Runtime(world.registry())
    expect_error(StaleAnchor, lambda: runtime.run("check", bundle=calendar_bundle(1, GENESIS)))
    assert not world.calls and runtime.budget.tool_calls == 0
    return {"ungranted_tool_rejected": True, "stale_bundle_rejected": True, "tool_calls": 0}


@check
def unknown_outcome_never_automatically_retried():
    calls = []

    def uncertain() -> dict:
        calls.append("side effect may have happened")
        raise TimeoutError("fixture lost response after dispatch")

    tools = make_registry([uncertain])
    trace = MemoryTrace()
    runtime = Runtime(tools, trace=trace)
    result = runtime.run("unknown fixture", bundle=bundle(effect_program("uncertain")))
    assert result.status == "interrupted_unknown" and len(calls) == 1
    restored = Runtime.restore(tools, trace)
    assert restored.run().status == "interrupted_unknown" and len(calls) == 1
    trace.resolve(
        0,
        {"status": "returned", "value": {"receipt": "externally verified"}},
        reason="Fixture operator verified the actual result",
    )
    restored = Runtime.restore(tools, trace)
    result = restored.run()
    assert result.status == "completed" and len(calls) == 1
    return {
        "before_resolution": "interrupted_unknown",
        "after_resolution": result.status,
        "total_dispatches": len(calls),
    }


@check
def sqlite_restore_after_settlement_before_checkpoint():
    calls = []

    def once() -> dict:
        calls.append("dispatch")
        return {"n": 41}

    tools = make_registry([once])
    with tempfile.TemporaryDirectory(prefix="flora-restore-") as directory:
        path = Path(directory) / "trace.sqlite"
        trace = SQLiteTrace(path)
        runtime = Runtime(tools, trace=trace)
        runtime.task = "restore gap fixture"
        runtime.install_bundle(bundle(effect_program("once")))
        runtime.executor.execute(
            {"tool": "once", "args": {}},
            epoch=0,
            trace_digest=GENESIS,
            before_dispatch=lambda event_id: runtime._save(),
        )
        # Durable receipt is newer than the checkpoint, as after a process crash.
        trace.close()
        reopened = SQLiteTrace(path)
        restored = Runtime.restore(tools, reopened)
        result = restored.run()
        assert result.status == "completed" and result.value == {"n": 41} and len(calls) == 1
        assert Runtime.restore(tools, reopened).run().status == "completed"
        assert len(calls) == 1
        reopened.close()
    return {"durable_restore": "completed", "dispatches_including_cached_restore": 1}


@check
def journal_hash_tampering_rejected():
    data = run_demo("calendar")["trace"]
    data["journal"][0]["payload"]["args"]["id"] = "A"
    expect_error(TraceIntegrityError, lambda: MemoryTrace.from_dict(data))
    return {"modified_journal_without_recomputed_chain": "rejected"}


@check
def replay_matches_history_only():
    data = run_demo("pagination")
    trace = MemoryTrace.from_dict(data["trace"])
    result = replay(pagination_program("data"), {}, trace.records)
    assert result["status"] == "completed" and result["matched_records"] == 2
    wrong = replay(move_program(930), {}, trace.records)
    assert wrong["status"] == "diverged" and wrong["matched_records"] == 0
    return {"matching": result["status"], "different_request": wrong["status"]}


@check
def contracts_three_valued_local_relations():
    good = pure(3)
    changed = pure(4)
    fault = pure(var("bad"), ops=[op("get", "bad", {}, "missing")])
    assert check_revision(good, ContextCheck("defined")).verdict == Verdict.PASS
    assert check_revision(fault, ContextCheck("fault")).verdict == Verdict.FAIL
    assert check_revision(good, ContextCheck("hyp", mode="hypothetical")).verdict == Verdict.UNKNOWN
    assert (
        check_revision(
            changed, ContextCheck("changed", relation="SAME_BOUNDARY", reference_program=good)
        ).verdict
        == Verdict.FAIL
    )
    return {
        "defined": "PASS",
        "pure_fault": "FAIL",
        "hypothetical_evidence": "UNKNOWN",
        "changed_boundary": "FAIL",
    }


@check
def empirical_guard_excludes_unknown():
    samples = [
        {"value": {"n": 1}, "verdict": "PASS"},
        {"value": {"n": 2}, "verdict": "FAIL"},
        {"value": {"n": 3}, "verdict": "UNKNOWN"},
    ]
    result = fit_guard(samples)
    assert result["requires_runtime_check"] is True
    assert evaluate_predicate(result["guard"], samples[0]["value"]) is True
    assert all(evaluate_predicate(result["guard"], x["value"]) is not True for x in samples[1:])
    return {"status": result["status"], "requires_runtime_check": True}


@check
def reuse_guard_still_requires_current_check():
    old = pure(var("result"), params=["n"], ops=[op("add", "result", var("n"), 1)])
    proposed = pure(3, params=["n"])
    migration = pure(
        var("mapped"), params=["context"], ops=[op("get", "mapped", var("context"), "inputs")]
    )
    source = new_machine(old, {"n": 2})
    guard = fit_guard([{"value": {"n": 2}, "verdict": "PASS"}])["guard"]
    library = ReuseLibrary()
    library.register(source, proposed, migration, guard)
    assert library.route(source, receipts=[], memory={}).accepted
    unseen = library.route(new_machine(old, {"n": 4}), receipts=[], memory={})
    assert not unseen.accepted
    assert unseen.report["attempts"][0]["guard"] is True
    assert unseen.report["attempts"][0]["verdict"] == "FAIL"
    return {
        "guard_matched_unseen_input": True,
        "current_boundary_check": "FAIL",
        "reuse_rejected": True,
    }


@check
def replan_preserves_actual_observation():
    calls = []

    def observe() -> dict:
        calls.append("observe")
        return {"n": 7}

    program = effect_program("observe")
    program["blocks"]["done"]["term"] = {
        "op": "replan",
        "reason": "Need remaining reasoning",
        "state": {"observed": var("value")},
    }

    def compile_fixture(context):
        if context.epoch == 0:
            return bundle(program, context.epoch, context.trace_digest)
        assert context.receipts[0]["value"] == {"n": 7}
        assert context.memory["__openharness_continuation__"]["state"]["observed"] == {"n": 7}
        return bundle(pure(14), context.epoch, context.trace_digest)

    compiler = ScriptedCompiler(compile_fixture)
    runtime = Runtime(make_registry([observe]), compiler=compiler)
    result = runtime.run("read once then replan")
    assert result.status == "completed" and result.value == 14 and calls == ["observe"]
    assert len(compiler.requests) == 2
    return {"scripted_compilations": 2, "external_model_calls": 0, "tool_calls": 1}


@check
def compiler_repair_is_bounded_and_accounted():
    class Provider:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, *, max_tokens):
            self.calls += 1
            text = "{" if self.calls == 1 else json.dumps(bundle(pure(42)))
            return ModelResponse(text, input_tokens=10, output_tokens=5)

    provider = Provider()
    result = Runtime(ToolRegistry([]), compiler=LLMCompiler(provider)).run("repair fixture")
    assert result.status == "completed" and result.value == 42
    assert provider.calls == result.budget["model_calls"] == 2
    assert result.budget["input_tokens"] == 20 and result.budget["output_tokens"] == 10
    return {"fixture_provider_calls_accounted": 2, "external_model_calls": 0, "result": 42}


@check
def completion_guard_rejects_early_return():
    runtime = Runtime(ToolRegistry([]), completion_guard=lambda: False)
    result = runtime.run("unfinished workflow", bundle=bundle(pure("done")))
    assert result.status == "needs_program"
    assert any(x["kind"] == "completion_rejected" for x in result.reports)
    return {"premature_return": "rejected", "status": result.status}


@check
def unlimited_general_defaults_not_unlimited_runtime():
    from flora.general.budgets import unlimited_defaults

    defaults = unlimited_defaults()
    assert all(value is None for value in defaults.values())
    assert BudgetLimits().max_model_calls == 30
    assert unlimited_defaults({"max_tool_calls": 0})["max_tool_calls"] == 0
    budget = Budget(BudgetLimits(**unlimited_defaults({"max_tool_calls": 0})))
    expect_error(BudgetExceeded, budget.before_tool_call)
    assert RuntimeConfig().max_steps == 200 and RuntimeConfig().max_compile_cycles == 30
    return {
        "general_cumulative": defaults,
        "kernel_default_model_calls": 30,
        "runtime_steps": 200,
        "runtime_compile_cycles": 30,
        "explicit_zero_enforced": True,
    }


@check
def session_reopen_keeps_usage_and_separates_turns():
    class Provider:
        def complete(self, messages, *, max_tokens):
            context = json.loads(messages[1]["content"])
            value = len(context["memory"]["history"]["turns"])
            return ModelResponse(
                json.dumps(bundle(pure(value), context["epoch"], context["trace_digest"])),
                input_tokens=10,
                output_tokens=5,
            )

    with tempfile.TemporaryDirectory(prefix="flora-session-") as directory:
        with Agent(provider=Provider(), session_dir=directory) as agent:
            assert agent.run("first").value == 0
            assert agent.status()["budget"]["model_calls"] == 1
        with Agent(provider=Provider(), session_dir=directory) as agent:
            assert agent.run("second").value == 1
            assert agent.status()["budget"]["model_calls"] == 2
            assert len(list(Path(directory).glob("turn-*.sqlite"))) == 2
    return {
        "separate_turn_journals": 2,
        "cumulative_fixture_provider_calls": 2,
        "external_model_calls": 0,
    }


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for fn in CHECKS)
