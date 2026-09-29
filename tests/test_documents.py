# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flora.general import _document_process as process
from flora.general.documents import MAX_DOCUMENT, extract_document, read_tables
from flora.support.errors import ValidationError


class DocumentWorkerTests(unittest.TestCase):
    def test_real_csv_and_text_worker(self):
        self.assertEqual(
            read_tables(b"name,count\na,17\nb,-2\n", ".csv"),
            {"Sheet1": [["name", "count"], ["a", "17"], ["b", "-2"]]},
        )
        self.assertEqual(read_tables(b"name,count\n", ".csv"), {"Sheet1": [["name", "count"]]})
        self.assertEqual(extract_document(b"hello\n", ".txt")[0], "hello\n")

    def test_input_and_validation_limits_retained(self):
        with self.assertRaisesRegex(ValidationError, "8 MiB"):
            read_tables(b" " * (MAX_DOCUMENT + 1), ".csv")
        with self.assertRaisesRegex(ValidationError, "CSV or XLSX"):
            read_tables(b"x", ".unknown")

    @unittest.skipUnless(sys.platform == "darwin", "macOS native API")
    def test_native_memory_reader(self):
        self.assertGreater(process._darwin_memory_reader()(os.getpid()), 0)

    def fake_process(self):
        child = Mock(pid=123, returncode=0)
        child.__enter__ = Mock(return_value=child)
        child.__exit__ = Mock(return_value=False)
        child.poll.return_value = None
        return child

    def test_mac_memory_over_limit_kills_and_reaps(self):
        child = self.fake_process()
        with (
            patch.object(sys, "platform", "darwin"),
            patch.object(
                process, "_darwin_memory_reader", return_value=lambda pid: process.MEMORY_LIMIT + 1
            ),
            patch.object(process.subprocess, "Popen", return_value=child),
        ):
            with self.assertRaisesRegex(ValidationError, "resident-memory limit"):
                process.run_worker(b"x", {})
        child.kill.assert_called_once()
        child.wait.assert_called_once()
        child.communicate.assert_not_called()

    def test_unavailable_monitor_never_launches(self):
        with (
            patch.object(sys, "platform", "darwin"),
            patch.object(
                process, "_darwin_memory_reader", side_effect=ValidationError("unavailable")
            ),
            patch.object(process.subprocess, "Popen") as spawn,
        ):
            with self.assertRaises(ValidationError):
                process.run_worker(b"x", {})
            spawn.assert_not_called()

    def test_monitor_read_failure_is_fail_closed(self):
        child = self.fake_process()
        with (
            patch.object(sys, "platform", "darwin"),
            patch.object(
                process,
                "_darwin_memory_reader",
                return_value=Mock(side_effect=ValidationError("denied")),
            ),
            patch.object(process.subprocess, "Popen", return_value=child),
        ):
            with self.assertRaisesRegex(ValidationError, "denied"):
                process.run_worker(b"x", {})
        child.kill.assert_called_once()
        child.wait.assert_called_once()

    def test_mac_timeout_always_kills_and_reaps(self):
        child = self.fake_process()
        with (
            patch.object(sys, "platform", "darwin"),
            patch.object(process, "_darwin_memory_reader", return_value=lambda pid: 0),
            patch.object(process.subprocess, "Popen", return_value=child),
            patch.object(process.time, "monotonic", side_effect=[1, 32]),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                process.run_worker(b"x", {})
        child.kill.assert_called_once()
        child.wait.assert_called_once()

    def test_transport_does_not_forward_secrets(self):
        with (
            patch.dict(os.environ, {"FLORA_TEST_SECRET": "not-a-real-credential"}),
            patch(
                "flora.general.documents.run_worker",
                return_value=subprocess.CompletedProcess([], 0, b'{"value":{}}'),
            ) as run,
        ):
            read_tables(b"a\n", ".csv")
        env = run.call_args.args[1]
        self.assertNotIn("FLORA_TEST_SECRET", env)
        self.assertTrue(Path(env["PYTHONPATH"]).is_absolute())
