"""Authored offline responses exercise actual Runtime gates; not live-model evidence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flora.integrations.providers import ModelResponse, TransportError
from tests.helpers import block, bundle, pure
from tests.live_revision_probe import PageWorld, main, run_case


def v(name):
    return {"var": name}


def op(name, dest, *args):
    return {"op": name, "dest": dest, "args": list(args)}


def repair_program(*, wrong_ack=False, wrong_total=False):
    carry = {"payload": v("payload"), "count": v("count")}
    program = {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(
                ["payload", "count"],
                [op("has", "old", v("payload"), "items")],
                {"op": "branch", "condition": v("old"), "yes": "old", "no": "new", "args": carry},
            ),
            "consume": block(
                ["reply", "count"],
                [op("get", "payload", v("reply"), "value")],
                {"op": "jump", "target": "main", "args": carry},
            ),
            "sum": block(
                ["payload", "count", "items"],
                [
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
            "done": block(
                ["total", "next"],
                term={"op": "return", "value": 999 if wrong_total else v("total")},
            ),
            "more": block(
                ["total", "next"],
                term={
                    "op": "effect",
                    "tool": "read_page",
                    "args": {
                        "page": v("next"),
                        "acknowledged_count": 0 if wrong_ack else v("total"),
                    },
                    "capture": {"count": v("total")},
                    "bind": "reply",
                    "resume": "consume",
                },
            ),
        },
    }
    for label, field in (("old", "items"), ("new", "records")):
        program["blocks"][label] = block(
            ["payload", "count"],
            [op("get", "items", v("payload"), field)],
            {"op": "jump", "target": "sum", "args": {**carry, "items": v("items")}},
        )
    return program


def migration():
    program = pure({"payload": v("payload"), "count": v("count")}, ["context"])
    program["blocks"]["main"]["ops"] = [
        op("get", "inputs", v("context"), "inputs"),
        op("get", "count", v("inputs"), "count"),
        op("get", "reply", v("inputs"), "reply"),
        op("get", "payload", v("reply"), "value"),
    ]
    return program


class FixtureProvider:
    def __init__(self, *, mode="EXTEND", wrong_ack=False, wrong_total=False, ordinary=False):
        self.mode, self.wrong_ack, self.wrong_total, self.ordinary = (
            mode,
            wrong_ack,
            wrong_total,
            ordinary,
        )
        self.on_event = None
        self.views = []

    def complete(self, messages, *, max_tokens):
        view = json.loads(messages[1]["content"])
        self.views.append(view)
        program = repair_program(wrong_ack=self.wrong_ack, wrong_total=self.wrong_total)
        response = bundle(program, view["epoch"], view["trace_digest"])
        # Incoming inputs are syntax-valid, but accepted migration must supply
        # actual historical/current state. Deliberately not the current answer.
        response["programs"][0]["inputs"] = {"payload": {"records": [], "next": None}, "count": 0}
        response["revisions"] = [
            {
                "id": "normalize_pages",
                "target_candidate": "main",
                "mode": self.mode,
                "program": program,
                "migration": migration(),
            }
        ]
        if self.ordinary:
            response = bundle(pure(5), view["epoch"], view["trace_digest"])
        return ModelResponse(json.dumps(response), 10, 20)


class LiveRevisionProbeTests(unittest.TestCase):
    def execute(self, provider, sizes=(2, 3)):
        with tempfile.TemporaryDirectory() as tmp:
            return run_case(
                provider,
                {"syntax": "block-list-v2", "prompt_style": "compact-v2", "max_repairs": 0},
                sizes,
                Path(tmp),
            )

    def test_real_mixed_history_current_and_activation(self):
        for sizes in ((2, 3), (0, 4), (3, 0)):
            with self.subTest(sizes=sizes):
                provider = FixtureProvider()
                row = self.execute(provider, sizes)
                self.assertTrue(row["grade"]["mechanism_passed"], row)
                self.assertEqual(len(row["host_calls"]), 2)
                self.assertEqual(len(provider.views), 1)
                self.assertEqual(row["result"]["budget"]["model_calls"], 1)
                self.assertEqual(row["result"]["value"], sum(sizes))
                checked = [e for e in row["events"] if e.get("kind") == "revision_checked"]
                self.assertEqual(
                    [r["relation"] for r in checked[0]["results"]],
                    ["SAME_BOUNDARY", "DEFINED_PREFIX"],
                )
                self.assertEqual(checked[0]["current_check"]["verdict"], "PASS")

    def test_changed_historical_ack_is_actually_rejected(self):
        row = self.execute(FixtureProvider(wrong_ack=True))
        self.assertFalse(row["grade"]["mechanism_passed"])
        checked = [e for e in row["events"] if e.get("kind") == "revision_checked"]
        self.assertTrue(checked)
        self.assertFalse(checked[0]["accepted"])
        self.assertEqual(checked[0]["results"][0]["verdict"], "FAIL")
        self.assertEqual(len(row["host_calls"]), 2)

    def test_defined_prefix_pass_does_not_prove_task_correctness(self):
        row = self.execute(FixtureProvider(wrong_total=True))
        self.assertFalse(row["grade"]["task_passed"])
        self.assertFalse(row["grade"]["mechanism_passed"])
        self.assertEqual(row["grade"]["linked_mixed_history_revisions"], ["normalize_pages"])

    def test_ordinary_correct_return_is_not_revision_evidence(self):
        row = self.execute(FixtureProvider(ordinary=True))
        self.assertTrue(row["grade"]["task_passed"])
        self.assertFalse(row["grade"]["mechanism_passed"])

    def test_preserve_does_not_extend_fault(self):
        row = self.execute(FixtureProvider(mode="PRESERVE"))
        self.assertFalse(row["grade"]["mechanism_passed"])
        self.assertFalse(
            any(e.get("accepted") for e in row["events"] if e.get("kind") == "revision_checked")
        )

    def test_http_access_failure_stops_remaining_rounds(self):
        for status in (401, 402, 403, 404):
            calls = []

            class Rejected:
                def __init__(self, **kwargs):
                    self.on_event = None

                def set_session_key(self, value):
                    pass

                def complete(self, messages, *, max_tokens):
                    calls.append(1)
                    raise TransportError("access denied", category="http", status=status)

            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                profile = root / "profile.json"
                profile.write_text(json.dumps({"provider": {}, "compiler": {}}))
                output = root / "output"
                argv = [
                    "probe",
                    "--model",
                    "deepseek-flash",
                    "--api-key-env",
                    "LOCAL_TEST_ONLY",
                    "--profile",
                    str(profile),
                    "--output",
                    str(output),
                    "--rounds",
                    "2",
                ]
                with (
                    patch("tests.live_revision_probe.OpenAICompatibleProvider", Rejected),
                    patch(
                        "tests.live_revision_probe.os.environ", {"LOCAL_TEST_ONLY": "local-only"}
                    ),
                    patch("sys.argv", argv),
                    patch("builtins.print"),
                ):
                    self.assertEqual(main(), 3)
                self.assertEqual(calls, [1])
                rows = json.loads((output / "results.json").read_text())
                self.assertEqual(rows[0]["access_rejected"], status)
                self.assertEqual(len(rows[0]["receipts"]), 2)
                self.assertEqual(rows[1]["status"], "not_run")

    def test_tool_does_not_judge_acknowledgement(self):
        world = PageWorld()
        self.assertEqual(world.read_page(1, 500), world.pages[1])
        self.assertEqual(world.calls[0]["args"]["acknowledged_count"], 500)


if __name__ == "__main__":
    unittest.main()
