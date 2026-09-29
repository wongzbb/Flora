# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from unittest.mock import patch

from flora.integrations.providers import _strict_json_loads
from flora.integrations.streaming import StreamFailure, read_completion
from tests.helpers import Clock, Response, frame, sse


class StreamingTests(unittest.TestCase):
    def read(self, raw, **options):
        self.events = []
        return read_completion(
            raw if hasattr(raw, "read1") else Response(raw),
            limit=options.pop("limit", 1000000),
            deadline=options.pop("deadline", float("inf")),
            loads=_strict_json_loads,
            emit=self.events.append,
            **options,
        )

    def failure(self, raw, category, reason, **options):
        with self.assertRaises(StreamFailure) as caught:
            self.read(raw, **options)
        self.assertEqual((caught.exception.category, caught.exception.reason), (category, reason))
        self.assertIn("wire_bytes", caught.exception.diagnostics)
        self.assertEqual(self.events[-1]["kind"], "model_stream_diagnostics")
        return caught.exception

    def test_complete_stream_with_usage_after_finish(self):
        data = self.read(
            sse(
                frame("{"),
                frame("}"),
                frame(finish="stop"),
                {"choices": [], "usage": {"completion_tokens": 2}},
                "[DONE]",
            )
        )
        self.assertEqual(data["choices"][0]["message"]["content"], "{}")
        self.assertEqual(data["usage"]["completion_tokens"], 2)

    def test_whitespace_outside_strings_stops(self):
        self.failure(
            sse(frame("{}"), frame(" " * 33), frame(finish="stop"), "[DONE]"),
            "model_no_progress",
            "json_whitespace_limit",
            json_mode=True,
            max_json_whitespace=32,
        )

    def test_strings_quotes_backslashes_and_unicode_across_chunks(self):
        text = json.dumps({"data": ' a"' + " " * 80 + "\\" + " " * 80 + "中文"}, ensure_ascii=False)
        raw = sse(*(frame(c) for c in text), frame(finish="stop"), "[DONE]")

        class TinyResponse(Response):
            def read1(self, n):
                return super().read1(min(n, 3))

        data = self.read(TinyResponse(raw), json_mode=True, max_json_whitespace=8)
        self.assertEqual(data["choices"][0]["message"]["content"], text)

    def test_no_json_guard_in_plain_text_mode(self):
        data = self.read(
            sse(frame(" " * 80), frame(finish="stop"), "[DONE]"), max_json_whitespace=8
        )
        self.assertEqual(len(data["choices"][0]["message"]["content"]), 80)

    def test_error_categories(self):
        cases = [
            (b"data: \xff\n\n", "stream_malformed", "invalid_utf8"),
            (sse("{"), "stream_malformed", "invalid_json"),
            (sse("[]"), "stream_malformed", "invalid_frame"),
            (sse({"choices": [], "usage": []}), "stream_malformed", "invalid_usage"),
            (sse({"choices": {}}), "stream_malformed", "invalid_choices"),
            (sse({"choices": [1]}), "stream_malformed", "invalid_choice"),
            (
                sse({"choices": [{"index": True, "delta": {}}]}),
                "stream_malformed",
                "invalid_choice",
            ),
            (sse({"choices": [{"delta": []}]}), "stream_malformed", "invalid_delta"),
            (sse(frame(123)), "stream_malformed", "invalid_text"),
            (sse(frame("\ud800")), "stream_malformed", "invalid_text"),
            (sse(frame(finish=3)), "stream_malformed", "invalid_finish"),
            (sse(frame(finish="stop"), frame(finish="stop")), "stream_malformed", "invalid_finish"),
            (sse(frame(finish="stop"), frame("x")), "stream_malformed", "invalid_text"),
            (
                sse({"choices": [{"delta": {"tool_calls": [{}]}}]}),
                "stream_unsupported",
                "non_program_delta",
            ),
            (sse("[DONE]"), "stream_interrupted", "missing_finish"),
            (sse(frame("{}"), frame(finish="stop")), "stream_interrupted", "early_eof"),
            (sse(frame("{")), "stream_interrupted", "early_eof"),
        ]
        for raw, category, reason in cases:
            with self.subTest(reason=reason, raw=raw[:50]):
                self.failure(raw, category, reason)

    def test_resource_limits_do_not_become_malformed(self):
        self.failure(sse(frame("{}")), "stream_limit", "wire_limit", limit=10)
        self.failure(b":" + b"x" * 65536, "stream_limit", "line_limit")

    def test_upstream_error_is_sanitized_and_auth_not_retryable(self):
        for status in (401, 403, 429, 503, "secret-payload"):
            err = self.failure(
                sse({"error": {"code": status, "message": "secret-payload"}}),
                "stream_upstream_error",
                "upstream_error",
            )
            self.assertEqual(err.retryable, status in (429, 503))
            self.assertNotIn("secret-payload", str(err) + json.dumps(err.diagnostics))
            self.assertNotIn("secret-payload", json.dumps(self.events))

    def timed(self, frames, clock, delay):
        class Timed:
            def read1(self, n):
                clock.sleep(delay)
                return next(frames, b"")

        return Timed()

    def test_reasoning_and_keepalive_cannot_postpone_first_program(self):
        for packet in (sse(frame(reasoning="thinking")), b": keepalive\n\n"):
            with self.subTest(packet=packet):
                clock = Clock()
                with patch("flora.integrations.streaming.time.monotonic", clock.monotonic):
                    response = self.timed(iter([packet] * 5), clock, 1)
                    self.failure(
                        response,
                        "model_no_progress",
                        "first_program_timeout",
                        first_program_timeout=3,
                        progress_timeout=10,
                    )
                    self.assertEqual(clock.now, 3)

    def test_idle_guard_after_program(self):
        clock = Clock()
        with patch("flora.integrations.streaming.time.monotonic", clock.monotonic):
            response = self.timed(iter([sse(frame("{")), b":ping\n\n", b":ping\n\n"]), clock, 1)
            self.failure(response, "model_no_progress", "output_idle_timeout", progress_timeout=2)
            self.assertEqual(clock.now, 3)

    def test_total_deadline_not_reset_by_meaningful_output(self):
        clock = Clock()
        with patch("flora.integrations.streaming.time.monotonic", clock.monotonic):
            response = self.timed(iter([sse(frame("x"))] * 4), clock, 1)
            with self.assertRaises(TimeoutError):
                self.read(response, deadline=3, progress_timeout=2)
            self.assertEqual(clock.now, 3)
            self.assertEqual(self.events[-1]["kind"], "model_stream_diagnostics")
