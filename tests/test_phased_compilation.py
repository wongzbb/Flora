# SPDX-License-Identifier: Apache-2.0
"""Opt-in phase organization uses the existing anchored, budgeted replan path."""

import json
import tempfile
import unittest
from pathlib import Path

from flora.engine.budget import Budget, BudgetLimits
from flora.engine.runtime import Runtime
from flora.general.agent import GeneralAgent
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler
from flora.language.prompts import focused_prompt, phased_prompt
from flora.state.trace import SQLiteTrace
from flora.support.errors import CompilerError
from tests.helpers import block, bundle, context, pure
from tests.test_frontend import observe
from tests.test_recovery import SequenceProvider


class PhaseProvider:
    def __init__(self):
        self.contexts = []

    def complete(self, messages, *, max_tokens):
        ctx = json.loads(messages[1]["content"])
        self.contexts.append(ctx)
        if not ctx["epoch"]:
            program = observe("publish")
            program["blocks"]["ok"]["term"] = {
                "op": "replan",
                "reason": "Continue the remaining phase",
                "state": {"observed": {"var": "result"}, "pending": ["deliver"]},
            }
        else:
            program = pure({"var": "value"})
            program["blocks"]["main"] = block(
                ops=[
                    {
                        "op": "read_memory",
                        "dest": "saved",
                        "args": ["__openharness_continuation__"],
                    },
                    {"op": "get", "dest": "state", "args": [{"var": "saved"}, "state"]},
                    {"op": "get", "dest": "value", "args": [{"var": "state"}, "observed"]},
                ],
                term={"op": "return", "value": {"var": "value"}},
            )
        program = [{"label": label, **b} for label, b in program["blocks"].items()]
        return ModelResponse(json.dumps(bundle(program, ctx["epoch"], ctx["trace_digest"])), 10, 20)


class PhasedCompilationTests(unittest.TestCase):
    def test_handoff_reopens_with_typed_state_and_no_effect_replay_or_budget_refund(self):
        calls = []
        value = {"number": 182, "unknown": None, "text": "182", "nested": [False, 0]}

        def publish() -> dict:
            calls.append(1)
            return value

        tools = make_registry([publish])
        provider = PhaseProvider()
        compiler = LLMCompiler(provider, syntax="block-list-v3", prompt_style="compact-v3")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "trace.sqlite"
            trace = SQLiteTrace(path)
            runtime = Runtime(
                tools,
                compiler=compiler,
                trace=trace,
                budget=Budget(BudgetLimits(max_model_calls=2, max_tool_calls=1)),
            )
            first = runtime.run("Publish then deliver the exact typed value", slice_steps=2)
            self.assertEqual(first.status, "yielded")
            self.assertEqual(first.budget["model_calls"], 1)
            self.assertFalse(
                next(r for r in first.reports if r["kind"] == "replan_requested")[
                    "information_gain"
                ]
            )
            trace.close()
            trace = SQLiteTrace(path)
            restored = Runtime.restore(tools, trace, compiler=compiler)
            final = restored.run()
            self.assertEqual((final.status, final.value), ("completed", value))
            self.assertEqual(calls, [1])
            self.assertEqual((final.budget["model_calls"], final.budget["tool_calls"]), (2, 1))
            self.assertEqual(
                (final.budget["input_tokens"], final.budget["output_tokens"]), (20, 40)
            )
            self.assertEqual(provider.contexts[1]["epoch"], 1)
            self.assertEqual(
                provider.contexts[1]["memory"]["__openharness_continuation__"]["state"]["pending"],
                ["deliver"],
            )
            self.assertEqual(restored.diagnostic_calls, 0)
            trace.close()

    def test_phased_profile_is_opt_in_and_preserves_application_identity(self):
        configs = Path(__file__).resolve().parents[1] / "configs"
        expected = json.loads((configs / "deepseek-live-task-completion.json").read_text())
        actual = json.loads((configs / "deepseek-live-phased.json").read_text())
        self.assertEqual(actual["compiler"].pop("prompt_style"), "compact-v3")
        expected["compiler"].pop("prompt_style")
        self.assertEqual(actual, expected)
        with tempfile.TemporaryDirectory() as root:
            profile = {
                "compiler": {"prompt_style": "compact-v3"},
                "general": {"require_task_completion": True},
            }
            with GeneralAgent(
                session_dir=Path(root) / "session",
                workspace=root,
                provider=SequenceProvider(),
                profile=profile,
            ) as app:
                identity = app.agent._fingerprint
                instructions = app.agent.instructions
                self.assertNotIn("Replan only when new semantic reasoning", instructions)
                self.assertIn("bounded executable phase handoff", instructions)
            with GeneralAgent(
                session_dir=Path(root) / "session", provider=SequenceProvider()
            ) as reopened:
                self.assertEqual(reopened.agent._fingerprint, identity)

    def test_prompt_and_repair_allow_phases_without_removing_mechanisms(self):
        legacy = focused_prompt("block-list-v3")
        phased = phased_prompt("block-list-v3")
        self.assertIn("Replan only for NEW semantic reasoning", legacy)
        self.assertNotIn("Replan only for NEW semantic reasoning", phased)
        for mechanism in ("DUAL CONTROL", "SYNTHESIZED CONSUMER CONTRACTS", "UNKNOWN"):
            self.assertIn(mechanism, phased)

        class RecordingProvider(SequenceProvider):
            messages = None

            def complete(self, messages, *, max_tokens):
                self.messages = messages
                return super().complete(messages, max_tokens=max_tokens)

        provider = RecordingProvider(ModelResponse("{}", 1, 1), ModelResponse("{}", 1, 1))
        compiler = LLMCompiler(provider, syntax="block-list-v3", prompt_style="compact-v3")
        with self.assertRaises(CompilerError):
            compiler.compile(context())
        self.assertIn("bounded phase handoff", provider.messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
