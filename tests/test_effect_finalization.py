# SPDX-License-Identifier: Apache-2.0
"""Fault injection after actual publication/dispatch must never authorize replay."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flora.engine.runtime import Runtime
from flora.general.browser import BrowserTools
from flora.general.documents import DocumentTools, DocumentWorkspace
from flora.general.storage import ObservationStore
from flora.general.web import WebTools
from flora.integrations.binding import make_registry
from flora.integrations.workspace import WorkspaceTools
from flora.state.trace import SQLiteTrace
from flora.support.errors import InterruptedEffect, ValidationError
from tests.helpers import block, bundle


def call_program(tool, args):
    return {
        "version": 1,
        "entry": "main",
        "blocks": {
            "main": block(
                term={
                    "op": "effect",
                    "tool": tool,
                    "args": args,
                    "resume": "done",
                    "bind": "reply",
                    "capture": {},
                }
            ),
            "done": block(["reply"], term={"op": "return", "value": {"var": "reply"}}),
        },
    }


class EffectFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_directory_fsync_failure_after_text_or_binary_publish_is_unknown(self):
        original = os.fsync

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected final directory fsync failure")
            return original(fd)

        for binary in (False, True):
            files = DocumentWorkspace(self.root) if binary else WorkspaceTools(self.root)
            path = "binary.bin" if binary else "text.txt"
            with self.subTest(binary=binary), patch("os.fsync", side_effect=fail_directory):
                with self.assertRaises(InterruptedEffect):
                    if binary:
                        files.create_bytes(path, b"actual bytes")
                    else:
                        files.write_file(path, "actual bytes", create=True)
            self.assertEqual((self.root / path).read_bytes(), b"actual bytes")

    def test_prepublication_fsync_failure_remains_a_known_failure(self):
        files = WorkspaceTools(self.root)
        with patch("os.fsync", side_effect=OSError("before publication")):
            with self.assertRaises(OSError):
                files.write_file("absent.txt", "unpublished", create=True)
        self.assertFalse((self.root / "absent.txt").exists())

    def test_cleanup_failure_after_publication_is_unknown(self):
        files = WorkspaceTools(self.root)
        original = os.unlink

        def fail_temporary(path, **kwargs):
            if str(path).startswith(".flora-write-"):
                raise OSError("injected cleanup failure")
            return original(path, **kwargs)

        with patch("os.unlink", side_effect=fail_temporary):
            with self.assertRaises(InterruptedEffect):
                files.write_file("published.txt", "actual", create=True)
        self.assertEqual((self.root / "published.txt").read_text(), "actual")

    def test_report_receipt_failure_stops_and_durable_resume_does_not_republish(self):
        store = ObservationStore(self.root)
        self.addCleanup(store.close)
        files = DocumentWorkspace(self.root)
        documents = DocumentTools(files, store, lambda: "fixture")
        registry = make_registry([documents.write_report])
        path = self.root / "trace.sqlite"
        trace = SQLiteTrace(path)
        with patch.object(store, "record_artifact", side_effect=OSError("receipt unavailable")):
            runtime = Runtime(registry, trace=trace)
            result = runtime.run(
                "Publish report once",
                bundle=bundle(
                    call_program(
                        "write_report",
                        {"path": "report.md", "content": "Actual report", "source_ids": []},
                    )
                ),
            )
        self.assertEqual(result.status, "interrupted_unknown")
        self.assertEqual((self.root / "report.md").read_text(), "Actual report\n")
        self.assertEqual(len(trace.records), 1)
        trace.close()
        with patch.object(files, "write_file", side_effect=AssertionError("replayed")):
            reopened = SQLiteTrace(path)
            try:
                restored = Runtime.restore(registry, reopened)
                self.assertEqual(restored.run().status, "interrupted_unknown")
                self.assertEqual(restored.budget.tool_calls, 1)
                self.assertEqual(len(reopened.records), 1)
            finally:
                reopened.close()

    def test_export_receipt_failure_is_unknown_but_source_metadata_failure_is_predispatch(self):
        store = ObservationStore(self.root)
        self.addCleanup(store.close)
        files = DocumentWorkspace(self.root)
        documents = DocumentTools(files, store, lambda: "fixture")
        (self.root / "input.txt").write_text("A report")
        with patch.object(store, "artifacts", side_effect=OSError("metadata unavailable")):
            with self.assertRaises(OSError):
                documents.export_document("input.txt", "absent.docx")
        self.assertFalse((self.root / "absent.docx").exists())
        with patch.object(store, "record_artifact", side_effect=ValidationError("quota")):
            with self.assertRaises(InterruptedEffect):
                documents.export_document("input.txt", "published.docx")
        self.assertTrue((self.root / "published.docx").read_bytes().startswith(b"PK"))

    def test_http_decode_failure_after_mutation_is_unknown(self):
        store = Mock()
        client = Mock()
        client.request.return_value = {
            "status": 201,
            "body": b"created",
            "redirects": [],
            "headers": {"content-type": "text/plain; charset=unknown-encoding"},
        }
        web = WebTools(
            store,
            client,
            services={"fixture": {"base_url": "https://example.test/", "methods": ["POST", "GET"]}},
        )
        with self.assertRaises(InterruptedEffect):
            web.http_request("fixture", "items", "POST", {"name": "one"})
        client.request.assert_called_once()
        with self.assertRaises(ValidationError):
            web.http_request("fixture", "items", "GET")
        store.record.assert_not_called()

    def test_browser_postdispatch_validation_failures_cannot_be_retried(self):
        browser = object.__new__(BrowserTools)
        browser.config = {"allow_actions": True}
        browser.handles = {"el-real": ("tab-real", Mock())}
        browser.handles["el-real"][1].evaluate.return_value = True
        browser._invalidate = Mock(side_effect=ValidationError("after dispatch"))
        handle = browser.handles["el-real"][1]
        with self.assertRaises(InterruptedEffect):
            browser._action("el-real")
        handle.click.assert_called_once()
        with self.assertRaises(ValidationError):
            browser._action("el-missing")
        self.assertEqual(handle.click.call_count, 1)

    def test_browser_navigation_snapshot_and_screenshot_receipt_failures_are_unknown(self):
        browser = object.__new__(BrowserTools)
        browser.policy, browser.context, browser.store = Mock(), Mock(), Mock()
        browser.pages = {}
        browser._snapshot = Mock(side_effect=ValidationError("oversized observation"))
        with self.assertRaises(InterruptedEffect):
            browser._open("https://example.test/")
        browser.context.new_page.return_value.goto.assert_called_once()
        browser.files = DocumentWorkspace(self.root)
        browser.task_key = lambda: "fixture"
        page = Mock()
        page.screenshot.return_value = b"actual screenshot bytes"
        browser._page = Mock(return_value=page)
        browser.policy.timeout = 1
        browser.store.record_artifact.side_effect = ValidationError("storage quota")
        with self.assertRaises(InterruptedEffect):
            browser._screenshot("tab-real", "screen.png")
        self.assertEqual((self.root / "screen.png").read_bytes(), b"actual screenshot bytes")


if __name__ == "__main__":
    unittest.main()
