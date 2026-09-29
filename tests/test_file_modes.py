# SPDX-License-Identifier: Apache-2.0
"""Explicit v3 file effects keep the same publication and optimistic-concurrency gates."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from flora.general.agent import GeneralAgent
from flora.general.documents import DocumentWorkspace
from flora.support.errors import InterruptedEffect, ValidationError
from tests.test_general import WorkspaceProvider


class FileModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = DocumentWorkspace(self.root)
        self.published = Mock()
        self.files._published_callback = self.published

    def test_create_update_actual_receipts_and_stale_hash(self):
        first = self.files.create_file("x.txt", "original")
        self.assertTrue(first["created"])
        self.published.assert_called_once_with(first)
        with self.assertRaises(FileExistsError):
            self.files.create_file("x.txt", "clobber")
        # The legacy API's exception identity is deliberately unchanged.
        with self.assertRaises(ValidationError):
            self.files.write_file("x.txt", "legacy clobber", create=True)
        self.assertEqual((self.root / "x.txt").read_text(), "original")
        second = self.files.update_file("x.txt", "changed", first["sha256"])
        self.assertFalse(second["created"])
        self.assertEqual(second["before_sha256"], first["sha256"])
        with self.assertRaisesRegex(ValidationError, "mismatch"):
            self.files.update_file("x.txt", "stale", first["sha256"])
        self.assertEqual((self.root / "x.txt").read_text(), "changed")
        self.assertEqual(self.published.call_count, 2)

    def test_append_preserves_bytes_and_only_adds_required_separator(self):
        for i, (before, after) in enumerate(
            [
                (b"", b"new\n"),
                (b"old", b"old\nnew\n"),
                (b"old\n", b"old\nnew\n"),
                (b"old\n\n", b"old\n\nnew\n"),
                (b"old\r\n", b"old\r\nnew\r\n"),
                (b"old\r", b"old\rnew\r"),
            ]
        ):
            name = f"{i}.txt"
            (self.root / name).write_bytes(before)
            observed = self.files.read_file(name)
            receipt = self.files.append_lines(name, ["new"], observed["sha256"])
            self.assertEqual((self.root / name).read_bytes(), after)
            self.assertEqual(receipt["sha256"], self.files.read_file(name)["sha256"])
        self.assertEqual(self.published.call_count, 6)

    def test_append_is_hash_checked_and_invalid_requests_do_not_mutate(self):
        receipt = self.files.create_file("x.txt", "你好\n")
        for lines in ([], ["a\nb"], ["a\rb"], [None], "x"):
            with self.assertRaises(ValidationError):
                self.files.append_lines("x.txt", lines, receipt["sha256"])
        with self.assertRaisesRegex(ValidationError, "mismatch"):
            self.files.append_lines("x.txt", ["new"], "0" * 64)
        with self.assertRaises(FileNotFoundError):
            self.files.append_lines("missing.txt", ["new"], receipt["sha256"])
        with self.assertRaises(ValidationError):
            self.files.create_file("../escape.txt", "bad")
        self.assertEqual((self.root / "x.txt").read_text(), "你好\n")
        self.published.assert_called_once()
        self.files.append_lines("x.txt", ["世界", ""], receipt["sha256"])
        self.assertEqual((self.root / "x.txt").read_text(), "你好\n世界\n\n")

    def test_receipt_failure_remains_an_unknown_effect(self):
        self.published.side_effect = OSError("receipt store unavailable")
        with self.assertRaises(InterruptedEffect):
            self.files.create_file("x.txt", "actually published")
        self.assertEqual((self.root / "x.txt").read_text(), "actually published")

    def test_only_new_v3_sessions_expose_explicit_modes(self):
        for protocol in ("general-v1", "general-v2", "general-v3"):
            with GeneralAgent(
                session_dir=self.root / protocol,
                workspace=self.root,
                profile={"general": {"protocol": protocol}},
                provider=WorkspaceProvider(),
            ) as app:
                tools = {t["name"]: t for t in app.agent.tools.descriptions()}
                self.assertEqual("write_file" in tools, protocol != "general-v3")
                for tool in ("create_file", "update_file", "append_lines"):
                    self.assertEqual(tool in tools, protocol == "general-v3")
                if protocol == "general-v3":
                    schema = tools["append_lines"]["parameters"]
                    self.assertEqual(set(schema["required"]), {"path", "lines", "expected_sha256"})
