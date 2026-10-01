# SPDX-License-Identifier: Apache-2.0
"""Full generated-program integration and disclosed compiler view checks."""

import json
import tempfile
import unittest
from pathlib import Path

from flora.engine.runtime import RuntimeConfig
from flora.examples import run_demo
from flora.general.agent import GeneralAgent
from flora.integrations.providers import ModelResponse
from flora.language.compiler import CompilerContext, LLMCompiler, validate_bundle
from flora.language.frontend import lower_bundle
from flora.language.prompts import focused_prompt
from flora.support.values import clone
from tests.helpers import block, bundle


def var(name):
    return {"var": name}


def get(dest, value, key):
    return {"op": "get", "dest": dest, "args": [value, key]}


def collaborate_program():
    blocks = {}

    def effect(label, params, ops, tool, args, target, capture):
        blocks[label] = block(
            params,
            ops,
            {
                "op": "effect",
                "tool": tool,
                "args": args,
                "resume": target,
                "bind": "reply",
                "capture": {n: var(n) for n in capture},
            },
        )

    effect(
        "main", [], [], "spawn_agent", {"task": "Read left.json", "name": "Left"}, "spawn_right", []
    )
    effect(
        "spawn_right",
        ["reply"],
        [get("row", var("reply"), "value"), get("left", var("row"), "agent_id")],
        "spawn_agent",
        {"task": "Read right.json", "name": "Right"},
        "join",
        ["left"],
    )
    effect(
        "join",
        ["reply", "left"],
        [get("row", var("reply"), "value"), get("right", var("row"), "agent_id")],
        "wait_agents",
        {"agent_ids": [var("left"), var("right")], "timeout": 5},
        "read_left",
        ["left", "right"],
    )
    effect(
        "read_left",
        ["reply", "left", "right"],
        [],
        "read_agent",
        {"agent_id": var("left"), "limit": 24000},
        "review_left",
        ["left", "right"],
    )
    unpack_left = [
        get("window", var("reply"), "value"),
        get("fingerprint", var("window"), "result_digest"),
        get("text", var("window"), "text"),
        {"op": "parse_json", "dest": "answer", "args": [var("text")]},
        get("value", var("answer"), "value"),
        get("a", var("value"), "count"),
    ]
    effect(
        "review_left",
        ["reply", "left", "right"],
        unpack_left,
        "review_agent",
        {
            "agent_id": var("left"),
            "result_digest": var("fingerprint"),
            "disposition": "accepted",
            "note": "Collected full result; verify original files before returning",
            "evidence": [],
        },
        "read_right",
        ["right", "a"],
    )
    effect(
        "read_right",
        ["reply", "right", "a"],
        [],
        "read_agent",
        {"agent_id": var("right"), "limit": 24000},
        "review_right",
        ["right", "a"],
    )
    unpack_right = [
        get("window", var("reply"), "value"),
        get("fingerprint", var("window"), "result_digest"),
        get("text", var("window"), "text"),
        {"op": "parse_json", "dest": "answer", "args": [var("text")]},
        get("value", var("answer"), "value"),
        get("b", var("value"), "count"),
    ]
    effect(
        "review_right",
        ["reply", "right", "a"],
        unpack_right,
        "review_agent",
        {
            "agent_id": var("right"),
            "result_digest": var("fingerprint"),
            "disposition": "accepted",
            "note": "Collected full result; verify original files before returning",
            "evidence": [],
        },
        "check_left",
        ["a", "b"],
    )
    effect(
        "check_left",
        ["reply", "a", "b"],
        [],
        "read_file",
        {"path": "left.json"},
        "check_right",
        ["a", "b"],
    )
    parse_left = [
        get("actual", var("reply"), "value"),
        get("text", var("actual"), "content"),
        {"op": "parse_json", "dest": "parsed", "args": [var("text")]},
        get("count", var("parsed"), "count"),
        {"op": "eq", "dest": "left_ok", "args": [var("a"), var("count")]},
    ]
    effect(
        "check_right",
        ["reply", "a", "b"],
        parse_left,
        "read_file",
        {"path": "right.json"},
        "compare",
        ["a", "b", "left_ok"],
    )
    blocks["compare"] = block(
        ["reply", "a", "b", "left_ok"],
        [
            get("actual", var("reply"), "value"),
            get("text", var("actual"), "content"),
            {"op": "parse_json", "dest": "parsed", "args": [var("text")]},
            get("count", var("parsed"), "count"),
            {"op": "eq", "dest": "right_ok", "args": [var("b"), var("count")]},
            {"op": "and", "dest": "ok", "args": [var("left_ok"), var("right_ok")]},
        ],
        {
            "op": "branch",
            "condition": var("ok"),
            "yes": "done",
            "no": "mismatch",
            "args": {"a": var("a"), "b": var("b")},
        },
    )
    blocks["done"] = block(
        ["a", "b"],
        [{"op": "add", "dest": "total", "args": [var("a"), var("b")]}],
        {"op": "return", "value": var("total")},
    )
    blocks["mismatch"] = block(
        ["a", "b"],
        term={"op": "return", "value": "Worker claim conflicts with actual file; unresolved"},
    )
    return {"version": 1, "entry": "main", "blocks": blocks}


class MultiProvider:
    def complete(self, messages, *, max_tokens):
        context = json.loads(messages[1]["content"])
        tools = {t["name"] for t in context["tools"]}
        if "spawn_agent" in tools:
            program = collaborate_program()
        else:
            path = "left.json" if "left.json" in context["task"] else "right.json"
            program = {
                "version": 1,
                "entry": "main",
                "blocks": {
                    "main": block(
                        term={
                            "op": "effect",
                            "tool": "read_file",
                            "args": {"path": path},
                            "resume": "done",
                            "bind": "reply",
                            "capture": {},
                        }
                    ),
                    "done": block(
                        ["reply"],
                        [
                            get("actual", var("reply"), "value"),
                            get("text", var("actual"), "content"),
                            {"op": "parse_json", "dest": "parsed", "args": [var("text")]},
                        ],
                        {"op": "return", "value": var("parsed")},
                    ),
                },
            }
        return ModelResponse(
            json.dumps(bundle(program, context["epoch"], context["trace_digest"])), 12, 8
        )


class ReliabilityIntegrationTests(unittest.TestCase):
    def test_parent_workers_join_review_and_verify_actual_files_in_program(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            (workspace / "left.json").write_text('{"count":17}')
            (workspace / "right.json").write_text('{"count":24}')
            with GeneralAgent(
                session_dir=Path(root) / "session",
                workspace=workspace,
                provider=MultiProvider(),
                profile={"general": {"subagents": {"enabled": True}}},
            ) as app:
                result = app.run("Use two readers and verify their claims before returning the sum")
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["value"], 41)
                self.assertEqual(result["budget"]["model_calls"], 1)
                workers = app.delegation.agent_status()["agents"]
                self.assertEqual(len(workers), 2)
                self.assertTrue(all(r["review"]["disposition"] == "accepted" for r in workers))
                self.assertTrue(
                    all(
                        r["budget"]["model_calls"] == 1 and r["budget"]["tool_calls"] == 1
                        for r in workers
                    )
                )
                self.assertTrue(any(r["kind"] == "consumer_check" for r in result["reports"]))
                self.assertTrue(result["completion_checks"]["ready"])

    def test_repeated_pure_replanning_stalls_without_effects_or_budget_reset(self):
        class Provider:
            def complete(self, messages, *, max_tokens):
                context = json.loads(messages[1]["content"])
                program = {
                    "version": 1,
                    "entry": "main",
                    "blocks": {
                        "main": block(
                            term={
                                "op": "replan",
                                "reason": "same reasoning, no observation",
                                "state": {},
                            }
                        )
                    },
                }
                return ModelResponse(
                    json.dumps(bundle(program, context["epoch"], context["trace_digest"])), 10, 5
                )

        with tempfile.TemporaryDirectory() as root:
            with GeneralAgent(
                session_dir=Path(root) / "session", workspace=root, provider=Provider()
            ) as app:
                first = app.run("Deliberate no-progress test")
                self.assertEqual(first["status"], "stalled")
                self.assertEqual(first["failure"]["code"], "no_progress")
                self.assertEqual(first["budget"]["tool_calls"], 0)
                self.assertEqual(first["budget"]["model_calls"], 8)
                second = app.resume()
                self.assertEqual(second["status"], "stalled")
                self.assertEqual(second["budget"]["model_calls"], 16)
                self.assertTrue(app.status()["requires_resume"])

    def test_large_receipt_projection_retains_index_and_discloses_unknown_payload(self):
        provider = MultiProvider()
        compiler = LLMCompiler(provider, syntax="block-list-v2", prompt_style="compact-v2")
        receipt = {"status": "returned", "tool": "read_file", "value": {"content": "x" * 20000}}
        context = CompilerContext(
            "task",
            [],
            1,
            "1" * 64,
            receipts=[receipt],
            reports=[{"kind": "model_call_started", "i": n} for n in range(100)],
        )
        original = clone(context.to_dict())
        view = json.loads(compiler.build_messages(context)[1]["content"])
        self.assertEqual(view["receipts"][0]["trace_index"], 0)
        self.assertNotIn("value", view["receipts"][0]["record"])
        self.assertTrue(view["receipts"][0]["payload_view"]["omitted"])
        self.assertEqual(view["visibility"]["receipt_payloads_omitted"], [0])
        self.assertEqual(view["visibility"]["reports_omitted"], 84)
        self.assertEqual(context.to_dict(), original)
        self.assertEqual(view["trace_digest"], "1" * 64)

    def test_focused_full_examples_lower_and_validate_with_diagnostics(self):
        prompt = focused_prompt()
        examples = [
            json.loads(line) for line in prompt.splitlines() if line.startswith('{"programs":')
        ]
        self.assertEqual(len(examples), 4)
        for item in examples:
            validate_bundle(lower_bundle(item, syntax="block-list-v2"))
        self.assertTrue(examples[2]["diagnostics"])
        self.assertLess(len(prompt), 14000)
        for rule in (
            "PASS/FAIL/UNKNOWN",
            "PRESERVE",
            "EXTEND",
            "CHANGE",
            "witness",
            "CURRENT",
            "UNKNOWN",
        ):
            self.assertIn(rule, prompt)

    def test_diagnostic_ablation_keeps_real_world_safety_and_changes_information_action(self):
        active = run_demo("calendar")
        inactive = run_demo("calendar", config=RuntimeConfig(enable_diagnostics=False))
        self.assertEqual(active["result"]["status"], "completed")
        self.assertEqual(inactive["result"]["status"], "completed")
        self.assertNotEqual(
            [r["tool"] for r in active["host_observation"]["calls"]],
            [r["tool"] for r in inactive["host_observation"]["calls"]],
        )
        self.assertEqual(active["host_observation"]["events"]["A"], 930)
        self.assertEqual(inactive["host_observation"]["events"]["A"], 870)

    def test_repeated_known_tool_error_invokes_repair_before_resending(self):
        from flora.engine.runtime import Runtime
        from flora.integrations.binding import make_registry
        from flora.support.errors import ValidationError
        from tests.test_long_tasks import sequence

        calls = []

        def blocked() -> dict:
            calls.append("blocked")
            raise ValidationError("This configured source is unavailable")

        def available() -> dict:
            calls.append("available")
            return {"actual": 42}

        class Compiler:
            def compile(self, context):
                self.report = next(
                    r for r in context.reports if r["kind"] == "repeated_effect_error"
                )
                return bundle(sequence("available", count=1), context.epoch, context.trace_digest)

        compiler = Compiler()
        runtime = Runtime(make_registry([blocked, available]), compiler=compiler)
        result = runtime.run(
            "Use an available source",
            bundle=bundle(sequence("blocked", count=20)),
            repeated_error_limit=3,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(calls, ["blocked"] * 3 + ["available"])
        self.assertFalse(compiler.report["effects_replayed"])
        self.assertEqual(result.epoch, 4)

    def test_expected_failures_can_finish_without_forced_recovery(self):
        from flora.engine.runtime import Runtime
        from flora.integrations.binding import make_registry
        from flora.support.errors import ValidationError
        from tests.test_long_tasks import sequence

        def blocked() -> dict:
            raise ValidationError("expected test failure")

        runtime = Runtime(make_registry([blocked]))
        result = runtime.run(
            "Observe three errors",
            bundle=bundle(sequence("blocked", count=3)),
            repeated_error_limit=3,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.budget["tool_calls"], 3)
        self.assertFalse(any(r["kind"] == "repeated_effect_error" for r in result.reports))
