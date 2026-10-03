# SPDX-License-Identifier: Apache-2.0
"""Offline validation of the live CLI probe's checks, NOT a real CLI/API test."""

import json
import tempfile
import unittest
from pathlib import Path

from flora.terminal.cli import apply_default_subagent_options
from tests.live_terminal_probe import assess_turn


class TerminalProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.original = b"Initial public note.\n"
        (self.workspace / "notes.txt").write_bytes(self.original)

    def assess(self, name, value, tools=(), statuses=None, status="completed"):
        statuses = statuses or ["returned"] * len(tools)
        result = {
            "status": status,
            "value": value,
            "reports": [
                {"kind": "tool_result", "tool": tool, "status": state}
                for tool, state in zip(tools, statuses, strict=True)
            ],
        }
        return assess_turn(name, result, self.workspace, original=self.original)

    def test_exact_read_result_not_substring_or_unevaluated_expression(self):
        valid = {"project": "CLI continuity fixture", "total": 4}
        for value in (valid, json.dumps(valid)):
            self.assertTrue(self.assess("read", value, ["read_file"]))
        for value in (
            {**valid, "total": 14},
            {**valid, "total": 4.5},
            {**valid, "total": True},
            {**valid, "total": "4"},
            {**valid, "debug": 4},
            {**valid, "total": {"op": "add", "args": [6, -2]}},
            "CLI continuity fixture: answer might be 4",
        ):
            self.assertFalse(self.assess("read", value, ["read_file"]))
        self.assertFalse(self.assess("read", valid, ["read_file"], ["raised"]))
        self.assertFalse(self.assess("read", valid))

    def test_greeting_is_not_arbitrary_nonempty_or_failed_result(self):
        self.assertTrue(self.assess("greet", "Hello! How can I help?"))
        self.assertTrue(self.assess("greet_again", "你好！"))
        self.assertFalse(self.assess("greet", "503 Service Unavailable"))
        self.assertFalse(self.assess("greet", "hello", status="needs_program"))
        self.assertFalse(self.assess("greet", "hello", ["read_file"]))

    def test_edit_needs_actual_bytes_successful_effect_and_path(self):
        self.assertFalse(self.assess("edit", "notes.txt", ["read_file", "append_lines"]))
        path = self.workspace / "notes.txt"
        for suffix in (b"AUDITED", b"AUDITED\n"):
            path.write_bytes(self.original + suffix)
            self.assertTrue(self.assess("edit", "notes.txt", ["read_file", "append_lines"]))
            self.assertFalse(self.assess("edit", "done", ["read_file", "append_lines"]))
            self.assertFalse(
                self.assess(
                    "edit", "notes.txt", ["read_file", "append_lines"], ["returned", "raised"]
                )
            )
        path.write_bytes(self.original + b"\nAUDITED\n")
        self.assertFalse(self.assess("edit", "notes.txt", ["read_file", "append_lines"]))

    def test_readback_needs_new_observation_and_exact_content(self):
        value = self.original.decode()
        self.assertTrue(self.assess("readback", value, ["read_file"]))
        self.assertFalse(self.assess("readback", value))
        self.assertFalse(self.assess("readback", value.rstrip(), ["read_file"]))

    def test_conditional_requires_failed_read_then_success_and_no_create(self):
        value = "CLI continuity fixture"
        self.assertTrue(
            self.assess("conditional", value, ["read_file"] * 2, ["raised", "returned"])
        )
        self.assertFalse(self.assess("conditional", value, ["read_file"] * 2))
        self.assertFalse(
            self.assess(
                "conditional", {"project": value}, ["read_file"] * 2, ["raised", "returned"]
            )
        )
        (self.workspace / "optional.json").write_text("{}")
        self.assertFalse(
            self.assess("conditional", value, ["read_file"] * 2, ["raised", "returned"])
        )

    def test_path_needs_workspace_observation_not_unsupported_claim(self):
        self.assertTrue(self.assess("path", str(self.workspace), ["workspace_context"]))
        self.assertFalse(self.assess("path", str(self.workspace)))
        self.assertFalse(self.assess("path", "/wrong/path", ["workspace_context"]))

    def test_interactive_defaults_expose_bounded_recursive_delegation(self):
        profile = {}
        options = apply_default_subagent_options(profile)
        self.assertEqual(options["max_depth"], 5)
        self.assertEqual(options["max_total_children"], 64)
        self.assertTrue(options["enabled"])
        explicit = {"subagents": {"enabled": True, "max_depth": 0}}
        self.assertEqual(apply_default_subagent_options(explicit)["max_depth"], 0)
