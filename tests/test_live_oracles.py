# SPDX-License-Identifier: Apache-2.0
"""The opt-in benchmark must not confuse completion with actual task success."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.live_general_probe import FIXTURE, assess, fixture_for_round, select_model


class LiveOracleTests(unittest.TestCase):
    def evaluate(self, name, value, tools=(), **extra):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "notes.txt").write_text("Synthetic public test data only.\nVERIFIED")
            row = {
                "case": name,
                "result": {"status": "completed", "value": value},
                "fixture_unchanged": True,
                **extra,
            }
            events = [{"channel": "tool/call", "tool": t} for t in tools]
            return assess(row, workspace, FIXTURE, events)

    def test_claimed_publication_without_file_is_failure(self):
        self.assertFalse(self.evaluate("write", "已写入 summary.json", ["read_file"])["passed"])
        self.assertFalse(
            self.evaluate("write", "summary.json", ["write_file"], write_correct=False)["passed"]
        )
        self.assertTrue(
            self.evaluate("write", "summary.json", ["read_file", "write_file"], write_correct=True)[
                "passed"
            ]
        )

    def test_arithmetic_and_read_must_match_real_fixture(self):
        self.assertTrue(self.evaluate("compute", 123)["passed"])
        self.assertFalse(self.evaluate("compute", "结果可能是 123")["passed"])
        self.assertFalse(
            self.evaluate("read", {"project": FIXTURE["project"], "total": 49}, ["read_file"])[
                "passed"
            ]
        )
        self.assertTrue(
            self.evaluate("read", {"project": FIXTURE["project"], "total": 50}, ["read_file"])[
                "passed"
            ]
        )

    def test_unevaluated_operation_not_a_user_answer(self):
        result = self.evaluate(
            "missing", {"op": "concat", "args": ["FileNotFoundError"]}, ["read_file"]
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["expression_leak"])

    def test_append_line_does_not_require_unspecified_final_newline(self):
        self.assertTrue(self.evaluate("edit", "notes.txt", ["read_file", "write_file"])["passed"])

    def test_research_is_not_automatically_declared_success(self):
        self.assertIsNone(
            self.evaluate("research", {"error": "network blocked"}, ["web_search"])["passed"]
        )
        self.assertTrue(self.evaluate("research", "report")["review_required"])
        self.assertEqual(fixture_for_round(2)["records"], [])
        self.assertNotEqual(fixture_for_round(1)["project"], FIXTURE["project"])


class HoldoutOracleTests(unittest.TestCase):
    def check(self, name, value, *, status="returned", extra_call=False):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "notes.txt").write_text("Synthetic public test data only.\n")
            (workspace / "optional.json").write_text(
                json.dumps({"project": "Preferred " + FIXTURE["project"]})
            )
            literal = {"op": "effect", "args": {"tool": "create_file", "path": "never-created.txt"}}
            (workspace / "code.json").write_text(json.dumps(literal))
            if name == "workspace" and value == "correct":
                value = {"workspace_root": str(workspace.resolve()), "process_cwd": os.getcwd()}
            if name == "literal" and value == "correct":
                value = literal
            tool = "workspace_context" if name == "workspace" else "read_file"
            row = {
                "case": name,
                "fixture_unchanged": True,
                "result": {
                    "status": "completed",
                    "value": value,
                    "reports": [{"kind": "tool_result", "tool": tool, "status": status}],
                },
            }
            path = "optional.json" if name == "branch_present" else "code.json"
            events = [{"channel": "tool/call", "tool": tool, "text": json.dumps({"path": path})}]
            if extra_call:
                events.append(
                    {
                        "channel": "tool/call",
                        "tool": "read_file",
                        "text": '{"path":"evidence.json"}',
                    }
                )
            return assess(row, workspace, FIXTURE, events)

    def test_explicit_locations_and_aggregate_require_exact_result_and_success(self):
        self.assertTrue(self.check("workspace", "correct")["passed"])
        self.assertFalse(self.check("workspace", os.getcwd())["passed"])
        expected = {
            "project": FIXTURE["project"],
            "total": 50,
            "positive_count": 3,
            "record_count": 3,
        }
        self.assertTrue(self.check("aggregate", expected)["passed"])
        self.assertFalse(self.check("aggregate", dict(expected, total=49))["passed"])
        self.assertFalse(self.check("aggregate", expected, status="raised")["passed"])

    def test_present_branch_must_not_read_fallback(self):
        expected = "Preferred " + FIXTURE["project"]
        self.assertTrue(self.check("branch_present", expected)["passed"])
        self.assertFalse(self.check("branch_present", expected, extra_call=True)["passed"])
        self.assertFalse(self.check("branch_present", FIXTURE["project"])["passed"])

    def test_literal_round_trip_is_data_not_automatic_expression_failure(self):
        result = self.check("literal", "correct")
        self.assertTrue(result["passed"])
        self.assertFalse(result["expression_leak"])
        self.assertFalse(self.check("literal", "done")["passed"])
        self.assertFalse(self.check("literal", "correct", status="raised")["passed"])


class ProbeModelSelectionTests(unittest.TestCase):
    def test_explicit_selection_does_not_discover_or_claim_listing(self):
        with patch("tests.live_general_probe.urllib.request.build_opener") as opener:
            result = select_model(
                "https://fixture.invalid/v1", "fixture", model="chosen-id", skip_discovery=True
            )
        opener.assert_not_called()
        self.assertEqual(result["selected"], "chosen-id")
        self.assertNotIn("available", result)
        self.assertIn("skipped", result["selection"])
        with self.assertRaises(ValueError):
            select_model("https://fixture.invalid/v1", "fixture", skip_discovery=True)

    def test_discovery_remains_default_and_rejects_unlisted_choice(self):
        with patch("tests.live_general_probe.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
                b'{"data":[{"id":"deepseek-fixture"}]}'
            )
            result = select_model("https://fixture.invalid/v1", "fixture")
            self.assertEqual(result["available"], ["deepseek-fixture"])
            self.assertEqual(result["selection"], "listed")
            with self.assertRaises(ValueError):
                select_model("https://fixture.invalid/v1", "fixture", model="not-listed")
