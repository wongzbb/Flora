"""Metrics record events and byte counts without interpreting task data."""

import json
import unittest
from unittest.mock import patch

from tests.live_reliability_probe import AccessFailureMonitor


class ProbeTelemetryTests(unittest.TestCase):
    def test_chunks_are_grouped_by_actor_and_request_with_no_text_in_metrics(self):
        with patch("tests.live_reliability_probe.time.monotonic", side_effect=[10.0, 12.25]):
            monitor = AccessFailureMonitor([])
            for actor, request, text in [
                ("main", "a", "秘密"),
                ("main", "a", "xx"),
                ("child", "a", "x"),
                ("main", "b", "abc"),
            ]:
                monitor(
                    {
                        "kind": "transcript",
                        "channel": "program",
                        "actor": actor,
                        "request": request,
                        "text": text,
                    }
                )
            monitor({"kind": "transcript", "channel": "reasoning", "text": "Not a source program"})
            monitor({"kind": "transcript", "channel": "compiler_rejected", "text": "Uninterpreted"})
            monitor({"kind": "transcript", "channel": "tool/call"})
            monitor({"kind": "transcript", "channel": "tool/call"})
            monitor({"kind": "replan_requested"})
            monitor({"kind": "subagent_event", "event": {"kind": "replan_requested"}})
            metrics = monitor.telemetry()
        self.assertEqual(metrics["first_tool_dispatch_seconds"], 2.25)
        self.assertEqual(metrics["generated_program_text_bytes"], 12)
        self.assertEqual(metrics["largest_generated_program_text_bytes"], 8)
        self.assertEqual(metrics["generations_with_program_text"], 3)
        self.assertEqual(metrics["validation_rejections"], 1)
        self.assertEqual(metrics["replans"], 2)
        self.assertNotIn("秘密", json.dumps(metrics, ensure_ascii=False))

    def test_no_tool_or_program_is_reported_as_absent_not_a_fast_success(self):
        monitor = AccessFailureMonitor([])
        metrics = monitor.telemetry()
        self.assertIsNone(metrics["first_tool_dispatch_seconds"])
        self.assertEqual(metrics["generations_with_program_text"], 0)


if __name__ == "__main__":
    unittest.main()
