# SPDX-License-Identifier: Apache-2.0
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.engine.runtime import Runtime
from flora.integrations.binding import make_registry
from flora.integrations.providers import ModelResponse
from flora.language.compiler import LLMCompiler
from flora.language.frontend import lower_bundle
from tests.helpers import bundle, context, pure
from tests.test_frontend import observe
from tests.test_recovery import SequenceProvider


def consumer_fault_program(tool, args):
    """Deliberate test fault AFTER a real effect; never injected in application code."""
    program = observe(tool)
    program["blocks"]["main"]["term"]["args"] = args
    program["blocks"]["ok"]["ops"] = [
        {"op": "get", "dest": "missing", "args": [{"var": "result"}, "not_a_real_field"]}
    ]
    return lower_bundle(bundle(program))


class ConsumerRecoveryTests(unittest.TestCase):
    def fixture(self):
        c = context()
        c.epoch = 4
        c.receipts = [
            {"tool": "arbitrary_tool", "status": "returned", "value": {"secret_data": n}}
            for n in range(4)
        ]
        c.previous_programs = [{"id": "main", "program": pure(None), "status": "FAULTED"}]
        c.reports = [
            {
                "kind": "local_execution",
                "status": "fault",
                "candidate": "main",
                "epoch": 4,
                "details": {"code": "MISSING_KEY", "block": "ok", "op_index": 0},
            }
        ]
        return c

    def messages(self, c, **options):
        compiler = LLMCompiler(
            SequenceProvider(), syntax="block-list-v2", prompt_style="compact-v1", **options
        )
        system, user = compiler.build_messages(c)
        return system["content"], json.loads(user["content"])

    def test_actual_current_fault_adds_only_original_visible_indices(self):
        c = self.fixture()
        before = copy.deepcopy(c.to_dict())
        system, view = self.messages(c, max_visible_receipts=2)
        recovery = view["compiler_recovery"]
        self.assertEqual([r["trace_index"] for r in recovery["settled_receipts"]], [2, 3])
        self.assertNotIn("secret_data", json.dumps(recovery))
        self.assertIn("not a fresh task", system)
        self.assertEqual(c.to_dict(), before)

    def test_data_words_stale_fault_unknown_and_inactive_status_do_not_trigger(self):
        for kind in ("data", "stale", "unknown", "active"):
            c = self.fixture()
            if kind == "data":
                c.reports = []
                c.receipts[0]["value"] = {"kind": "local_execution", "status": "fault"}
            elif kind == "stale":
                c.reports[0]["epoch"] = 3
            elif kind == "unknown":
                c.reports[0]["status"] = "unknown"
            else:
                c.previous_programs[0]["status"] = "ACTIVE"
            system, view = self.messages(c)
            self.assertNotIn("compiler_recovery", view)
            self.assertNotIn("CURRENT LOCAL-CONSUMER RECOVERY", system)

    def test_unsettled_and_omitted_receipts_never_reappear(self):
        c = self.fixture()
        c.receipts[3]["status"] = "interrupted_unknown"
        _, view = self.messages(c, max_visible_receipts=1)
        self.assertEqual(view["compiler_recovery"]["settled_receipts"], [])
        _, view = self.messages(c, max_visible_receipts=0)
        self.assertEqual(view["compiler_recovery"]["settled_receipts"], [])
        c.receipts[0]["value"] = "omitted" * 10000
        _, view = self.messages(c, max_context_bytes=4000)
        self.assertGreater(view["visibility"]["receipts_omitted"], 0)
        self.assertLessEqual(len(json.dumps(view, separators=(",", ":")).encode()), 4000)
        visible = {r["trace_index"] for r in view["receipts"]}
        self.assertTrue(
            all(r["trace_index"] in visible for r in view["compiler_recovery"]["settled_receipts"])
        )

    def test_legacy_syntax_has_no_new_recovery_projection(self):
        c = self.fixture()
        for syntax, style in (
            ("ir-v1", "full-v1"),
            ("observe-v1", "compact-v1"),
            ("block-list-v1", "compact-v1"),
        ):
            messages = LLMCompiler(
                SequenceProvider(), syntax=syntax, prompt_style=style
            ).build_messages(c)
            self.assertNotIn("compiler_recovery", json.loads(messages[1]["content"]))
            self.assertNotIn("CURRENT LOCAL-CONSUMER RECOVERY", messages[0]["content"])

    def test_real_receipt_can_repair_consumer_without_repeating_effect(self):
        calls, seen = [], []

        def publish_once():
            calls.append("published")
            return {"path": "actual-artifact.txt"}

        class RepairProvider:
            def complete(self, messages, *, max_tokens):
                view = json.loads(messages[1]["content"])
                seen.append(view)
                # Fixture response, NOT application-generated code or a real-API test.
                repaired = pure(
                    {
                        "op": "get",
                        "args": [
                            {"op": "get", "args": [{"op": "read_receipt", "args": [0]}, "value"]},
                            "path",
                        ],
                    }
                )
                result = bundle(repaired, view["epoch"], view["trace_digest"])
                return ModelResponse(json.dumps(result), 1, 1)

        compiler = LLMCompiler(RepairProvider(), syntax="block-list-v2", prompt_style="compact-v1")
        runtime = Runtime(make_registry([publish_once]), compiler=compiler)
        result = runtime.run(
            "Publish once and return its actual path",
            bundle=consumer_fault_program("publish_once", {}),
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.value, "actual-artifact.txt")
        self.assertEqual(calls, ["published"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["compiler_recovery"]["settled_receipts"][0]["trace_index"], 0)
        self.assertTrue(any(r.get("kind") == "local_execution" for r in result.reports))


class RecoveryHarnessTests(unittest.TestCase):
    def test_entire_opt_in_harness_offline_preflight(self):
        from tests.live_consumer_recovery_probe import main

        class LocalProvider:
            def __init__(self, **kwargs):
                self.calls = 0

            def set_session_key(self, value):
                pass

            def complete(self, messages, *, max_tokens):
                self.calls += 1
                view = json.loads(messages[1]["content"])
                self_test = view["compiler_recovery"]["settled_receipts"][0]
                assert self_test["trace_index"] == 0
                receipt = view["receipts"][0]["record"]
                value = receipt["value"]
                answer = (
                    json.loads(value["content"])["project"]
                    if receipt["tool"] == "read_file"
                    else value["path"]
                )
                return ModelResponse(
                    json.dumps(bundle(pure(answer), view["epoch"], view["trace_digest"])), 1, 1
                )

        with tempfile.TemporaryDirectory() as root:
            output = Path(root, "test-run")
            argv = [
                "probe",
                "--base-url",
                "https://api.deepseek.com",
                "--model",
                "deepseek-flash",
                "--profile",
                str(Path(__file__).parents[1] / "configs/deepseek-fast.json"),
                "--output",
                str(output),
                "--rounds",
                "1",
            ]
            with (
                patch("sys.argv", argv),
                patch(
                    "tests.live_consumer_recovery_probe.getpass.getpass",
                    return_value="offline-fixture-key",
                ),
                patch("tests.live_consumer_recovery_probe.OpenAICompatibleProvider", LocalProvider),
                patch("builtins.print"),
            ):
                main()
            rows = json.loads((output / "results.json").read_text())
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["passed"] for r in rows))
            self.assertTrue(all(len(r["receipts"]) == 1 for r in rows))
            self.assertTrue(all(r["result"]["budget"]["model_calls"] == 1 for r in rows))
