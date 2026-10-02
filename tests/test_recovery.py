# SPDX-License-Identifier: Apache-2.0
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from flora.engine.runtime import Runtime
from flora.integrations.providers import ModelResponse, OpenAICompatibleProvider, TransportError
from flora.integrations.streaming import StreamFailure
from flora.integrations.tools import ToolRegistry
from flora.language.compiler import (
    LLMCompiler,
    _single_extra_delimiter_bundle,
    _single_missing_delimiter_bundle,
)
from flora.support.errors import CompilerError, StaleAnchor, ValidationError
from tests.helpers import Clock, Response, bundle, context, frame, sse


def json_response(content=None, finish="stop", reasoning=None):
    message = {"content": json.dumps(bundle()) if content is None else content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return Response(
        json.dumps(
            {
                "choices": [{"message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode(),
        "application/json",
    )


class SequenceProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    def complete(self, messages, *, max_tokens):
        self.calls += 1
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class RecoveryTests(unittest.TestCase):
    def provider(self, *responses, **options):
        p = OpenAICompatibleProvider(
            model="fixture", api_key_env=None, base_url="http://localhost/v1", **options
        )
        p._opener = Mock()
        p._opener.open.side_effect = responses
        return p

    def compiler(self, p, **options):
        self.usage, self.events = [], []
        c = LLMCompiler(p, on_usage=self.usage.append, **options)
        c.transport_retries = 2
        c.on_event = self.events.append
        return c

    def test_empty_length_is_not_format_repaired(self):
        for meta in ({}, {"reasoning_tokens": 12000}, {"reasoning_bytes": 50}):
            with self.subTest(meta=meta):
                p = SequenceProvider(
                    ModelResponse("  ", 10, 12000, raw_metadata={"finish_reason": "length", **meta})
                )
                c = self.compiler(p)
                with self.assertRaises(TransportError) as caught:
                    c.compile(context())
                self.assertEqual(p.calls, 1)
                self.assertEqual(len(self.usage), 1)
                self.assertEqual(self.usage[0]["output_tokens"], 12000)
                self.assertEqual(
                    caught.exception.category, "reasoning_exhausted" if meta else "empty_truncation"
                )
                self.assertFalse(any(e["kind"] == "compiler_rejected" for e in self.events))

    def test_delimiter_diagnostic_does_not_repair_or_execute_model_output(self):
        from flora.language.compiler import _delimiter_error

        malformed = '{"programs":[}]}'
        requests = []

        class Recording(SequenceProvider):
            def complete(self, messages, *, max_tokens):
                requests.append(messages)
                return super().complete(messages, max_tokens=max_tokens)

        provider = Recording(
            ModelResponse(malformed, 1, 1), ModelResponse(json.dumps(bundle()), 1, 1)
        )
        self.assertEqual(self.compiler(provider).compile(context())["incumbent"], "main")
        repair = json.loads(requests[1][-1]["content"])
        self.assertEqual(
            repair["syntax_window"]["delimiter_error"],
            {
                "offset": 13,
                "found": "}",
                "expected": "]",
                "opening_offset": 12,
            },
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(self.usage), 2)
        self.assertIsNone(_delimiter_error(json.dumps({"text": 'brackets ] } and quote " and \\'})))
        rejected = self.compiler(SequenceProvider(ModelResponse(malformed, 1, 1)), max_repairs=0)
        with self.assertRaises(CompilerError):
            rejected.compile(context())

    def test_single_extra_closer_is_repaired_only_for_a_complete_bundle(self):
        source = json.dumps(bundle())
        marker = "}}], \"incumbent\""
        malformed = source.replace(marker, "}}}], \"incumbent\"", 1)
        repaired = _single_extra_delimiter_bundle(malformed)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired[0], bundle())
        self.assertIsNone(_single_extra_delimiter_bundle('{"programs":[}]}'))

    def test_single_missing_closer_is_repaired_only_when_candidate_is_complete(self):
        source = json.dumps(
            {
                "programs": [{"id": "x"}],
                "incumbent": "main",
                "diagnostics": [],
                "expected_epoch": 0,
                "expected_digest": "",
            }
        )
        malformed = source.replace("], \"incumbent\"", "}, \"incumbent\"", 1)
        repaired = _single_missing_delimiter_bundle(malformed)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired[0], json.loads(source))
        self.assertIsNone(_single_missing_delimiter_bundle('{"programs":[}]}'))

    def test_reasoning_fallback_requires_explicit_different_profile(self):
        p = self.provider(
            json_response("", "length", "thought"),
            json_response(),
            reasoning_fallback_options={"reasoning_effort": "low"},
        )
        c = self.compiler(p)
        self.assertEqual(c.compile(context())["incumbent"], "main")
        payloads = [json.loads(call.args[0].data) for call in p._opener.open.call_args_list]
        self.assertNotIn("reasoning_effort", payloads[0])
        self.assertEqual(payloads[1]["reasoning_effort"], "low")
        self.assertEqual(len(self.usage), 2)
        self.assertEqual(sum(x["output_tokens"] for x in self.usage), 10)
        # The same exhausted profile cannot keep getting reactivated.
        err = TransportError("fixture", category="reasoning_exhausted")
        self.assertFalse(p.adapt(err))

    def test_no_default_reasoning_switch(self):
        p = self.provider(json_response("", "length", "thought"))
        with self.assertRaises(TransportError):
            self.compiler(p).compile(context())
        self.assertEqual(p._opener.open.call_count, 1)

    def test_stream_fallback_once_and_only_for_malformed(self):
        p = self.provider(Response(sse("{")), json_response(), stream=True, stream_fallback=True)
        self.compiler(p).compile(context())
        payloads = [json.loads(call.args[0].data) for call in p._opener.open.call_args_list]
        self.assertEqual([x["stream"] for x in payloads], [True, False])
        self.assertNotIn("stream_options", payloads[1])
        self.assertFalse(p.adapt(TransportError("fixture", category="stream_malformed")))
        for category in (
            "stream_limit",
            "stream_unsupported",
            "stream_upstream_error",
            "model_no_progress",
            "stream_interrupted",
        ):
            with self.subTest(category=category):
                fresh = self.provider(stream=True, stream_fallback=True)
                self.assertFalse(fresh.adapt(TransportError("fixture", category=category)))
        fresh = self.provider(stream=True)
        self.assertFalse(fresh.adapt(TransportError("fixture", category="stream_malformed")))

    def test_explicit_idle_fallback_is_once_accounted_and_discards_partial_program(self):
        error = StreamFailure("model_no_progress", "output_idle_timeout")
        error.diagnostics = {"reason": "output_idle_timeout", "program_bytes": 8}
        p = self.provider(
            Response(b"partial"), json_response(), stream=True, stream_idle_fallback=True
        )
        with patch("flora.integrations.streaming.read_completion", side_effect=error):
            result = Runtime(ToolRegistry([]), compiler=self.compiler(p)).run("fixture")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.value, 42)
        self.assertEqual(result.budget["model_calls"], 2)
        self.assertEqual(result.budget["tool_calls"], 0)
        payloads = [json.loads(call.args[0].data) for call in p._opener.open.call_args_list]
        self.assertEqual([x["stream"] for x in payloads], [True, False])
        idle = TransportError("idle", category="model_no_progress", diagnostics=error.diagnostics)
        self.assertFalse(p.adapt(idle))
        self.assertFalse(self.provider(stream=True).adapt(idle))

    def test_idle_fallback_never_applies_to_reasoning_whitespace_or_auth(self):
        for reason in ("first_program_timeout", "json_whitespace_limit", "unknown"):
            p = self.provider(stream=True, stream_idle_fallback=True)
            self.assertFalse(
                p.adapt(
                    TransportError(
                        "fixture", category="model_no_progress", diagnostics={"reason": reason}
                    )
                )
            )
        with self.assertRaises(ValidationError):
            self.provider(stream_idle_fallback=1)

    def test_provider_progress_guard_never_executes_or_retries_partial_program(self):
        p = self.provider(
            Response(sse(frame("{"), frame(" " * 40))),
            stream=True,
            stream_fallback=True,
            max_json_whitespace=32,
            request_options={"response_format": {"type": "json_object"}},
        )
        c = self.compiler(p)
        result = Runtime(ToolRegistry([]), compiler=c).run("fixture")
        self.assertEqual(result.budget["tool_calls"], 0)
        self.assertEqual(p._opener.open.call_count, 1)
        self.assertNotEqual(result.status, "completed")
        event = next(e for e in self.events if e["kind"] == "model_failure")
        self.assertEqual(event["category"], "model_no_progress")
        self.assertEqual(event["diagnostics"]["reason"], "json_whitespace_limit")

    def test_auth_failure_not_retried_and_body_not_leaked(self):
        for response in (
            urllib.error.HTTPError("http://localhost/v1", 401, "secret", {}, io.BytesIO(b"secret")),
            Response(sse({"error": {"code": 401, "message": "secret"}})),
        ):
            with self.subTest(response=type(response).__name__):
                p = self.provider(response, stream=True, stream_fallback=True)
                with self.assertRaises(TransportError) as caught:
                    self.compiler(p).compile(context())
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(p._opener.open.call_count, 1)
                self.assertNotIn("secret", str(caught.exception) + json.dumps(self.events))

    def test_provider_accepts_bounded_stream_overhead_without_lifting_output_limit(self):
        raw = sse(
            *(frame(reasoning="x") for _ in range(200)), frame("{}"), frame(finish="stop"), "[DONE]"
        )
        p = self.provider(Response(raw), stream=True, max_response_bytes=202)
        self.assertEqual(
            p.complete([{"role": "user", "content": "fixture"}], max_tokens=300).text, "{}"
        )
        for options, expected in (
            ({"max_response_bytes": 201}, "decoded_limit"),
            ({"max_stream_bytes": 100}, "wire_limit"),
        ):
            p = self.provider(Response(raw), stream=True, **options)
            with self.assertRaises(TransportError) as caught:
                p.complete([{"role": "user", "content": "fixture"}], max_tokens=300)
            self.assertEqual(caught.exception.diagnostics["reason"], expected)
            self.assertEqual(p._opener.open.call_count, 1)

    def test_transport_classification_includes_stream_limits_and_auth(self):
        from flora.general.reliability import failure_info

        for reason in (
            "model stream_limit: wire_limit; partial program discarded",
            "model stream_malformed: invalid_json; partial program discarded",
            "output budget exhausted before a program was produced",
        ):
            self.assertEqual(failure_info("needs_program", reason)["code"], "model_transport")
        for status in (401, 402, 403, 404):
            p = self.provider(
                Response(sse({"error": {"code": status, "message": "secret"}})), stream=True
            )
            with self.assertRaises(TransportError) as caught:
                p.complete([{"role": "user", "content": "fixture"}], max_tokens=300)
            info = failure_info("needs_program", str(caught.exception))
            self.assertEqual(info["http_status"], status)
            self.assertEqual(info["code"], "model_transport")
            self.assertNotIn("secret", json.dumps(info))

    def test_recovery_budget_shared_with_format_repair(self):
        transient = TransportError("fixture", category="connection", retryable=True)
        p = SequenceProvider(transient, ModelResponse("{", 10, 5), transient, transient)
        c = self.compiler(p)
        with patch("flora.language.compiler.time.sleep"):
            with self.assertRaises(TransportError):
                c.compile(context())
        self.assertEqual(p.calls, 4)  # original + one repair + two recoveries, not 3 per phase
        self.assertEqual(len(self.usage), 4)
        self.assertEqual(sum(e["kind"] == "model_retry" for e in self.events), 2)

    def test_compilation_deadline_shared_by_retries_and_repair(self):
        clock = Clock()
        error = TransportError("fixture", category="connection", retryable=True, retry_after=4)
        p = SequenceProvider(error, ModelResponse("{", 10, 5), error)
        c = self.compiler(p, compilation_timeout=5)
        with (
            patch("flora.language.compiler.time.monotonic", clock.monotonic),
            patch("flora.language.compiler.time.sleep", clock.sleep),
        ):
            with self.assertRaisesRegex(CompilerError, "compilation_deadline"):
                c.compile(context())
        self.assertEqual(p.calls, 3)
        self.assertEqual(clock.now, 4)
        self.assertEqual(len(self.usage), 3)

    def test_deadline_not_serialized_and_expired_zero_not_dispatched(self):
        p = self.provider(json_response())
        with self.assertRaises(TransportError) as caught:
            p.complete([{"role": "user", "content": "fixture"}], max_tokens=50, request_deadline=0)
        self.assertEqual(caught.exception.category, "compilation_deadline")
        self.assertEqual(p._opener.open.call_count, 0)
        clock = Clock()
        with patch("flora.integrations.providers.time.monotonic", clock.monotonic):
            p.complete([{"role": "user", "content": "fixture"}], max_tokens=50, request_deadline=5)
        request = p._opener.open.call_args.args[0]
        self.assertNotIn("request_deadline", json.loads(request.data))
        self.assertEqual(p._opener.open.call_args.kwargs["timeout"], 5)

    def test_custom_provider_no_new_required_keyword_and_deadline_checked_afterwards(self):
        clock = Clock()

        class Provider:
            def complete(self, messages, *, max_tokens):
                clock.sleep(5)
                return ModelResponse(json.dumps(bundle()), 10, 5)

        c = self.compiler(Provider(), compilation_timeout=2)
        with patch("flora.language.compiler.time.monotonic", clock.monotonic):
            with self.assertRaisesRegex(CompilerError, "compilation_deadline"):
                c.compile(context())
        self.assertEqual(len(self.usage), 1)

    def test_stale_anchor_cannot_be_repaired(self):
        p = SequenceProvider(ModelResponse(json.dumps(bundle(epoch=1)), 10, 5))
        with self.assertRaises(StaleAnchor):
            self.compiler(p).compile(context())
        self.assertEqual(p.calls, 1)

    def test_usage_callback_errors_cannot_trigger_retry(self):
        p = SequenceProvider(ModelResponse(json.dumps(bundle()), 10, 5))
        c = self.compiler(p)
        c.on_usage = Mock(side_effect=RuntimeError("accounting failed"))
        with self.assertRaisesRegex(RuntimeError, "accounting failed"):
            c.compile(context())
        self.assertEqual(p.calls, 1)

    def test_context_bound_includes_syntax_marker(self):
        p = SequenceProvider()
        c = LLMCompiler(p, syntax="observe-v1")
        size = len(c.build_messages(context())[1]["content"].encode())
        c.max_context_bytes = size - 1
        with self.assertRaises(CompilerError):
            c.build_messages(context())

    def test_invalid_options_and_tool_injection_rejected(self):
        for option in (
            {"first_program_timeout": 0},
            {"progress_timeout": True},
            {"max_json_whitespace": -1},
            {"stream_fallback": 1},
            {"reasoning_fallback_options": {"tools": []}},
            {"request_options": {"request_deadline": 9}},
        ):
            with self.subTest(option=option), self.assertRaises(ValidationError):
                self.provider(**option)
        for option in ({"syntax": []}, {"compilation_timeout": float("inf")}):
            with self.subTest(option=option), self.assertRaises(ValidationError):
                LLMCompiler(SequenceProvider(), **option)

    def test_identical_reasoning_fallback_not_retried(self):
        options = {"reasoning_effort": "low"}
        p = self.provider(request_options=options, reasoning_fallback_options=options)
        self.assertFalse(p.adapt(TransportError("fixture", category="reasoning_exhausted")))

    def test_supported_http_feature_fallback_still_budgeted(self):
        error = urllib.error.HTTPError(
            "http://localhost/v1",
            400,
            "fixture",
            {},
            io.BytesIO(json.dumps({"error": {"param": "stream_options"}}).encode()),
        )
        p = self.provider(error, json_response(), stream=True)
        self.compiler(p).compile(context())
        calls = [json.loads(c.args[0].data) for c in p._opener.open.call_args_list]
        self.assertIn("stream_options", calls[0])
        self.assertNotIn("stream_options", calls[1])
        self.assertTrue(calls[1]["stream"])
        self.assertEqual(len(self.usage), 2)

    def test_deadline_includes_validation(self):
        clock = Clock()
        p = SequenceProvider(ModelResponse(json.dumps(bundle()), 10, 5))
        c = self.compiler(p, compilation_timeout=5)

        def validate(*args, **kwargs):
            clock.sleep(6)
            return bundle()

        with (
            patch("flora.language.compiler.time.monotonic", clock.monotonic),
            patch("flora.language.compiler.validate_bundle", validate),
        ):
            with self.assertRaisesRegex(CompilerError, "compilation_deadline"):
                c.compile(context())
        self.assertEqual(len(self.usage), 1)
