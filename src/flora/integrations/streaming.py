# SPDX-License-Identifier: Apache-2.0
"""Bounded SSE decoding. Partial output and reasoning are never executable programs."""

from __future__ import annotations

import time


class StreamFailure(ValueError):
    """A local reason code and counters only; never retains a remote error body."""

    def __init__(self, category, reason, *, retryable=False, status=None):
        super().__init__(reason)
        self.category, self.reason = category, reason
        self.retryable, self.status = retryable, status
        self.diagnostics = {}


def chunks(response, limit, deadline, idle_timeout, *, monitor=None):
    wire = 0
    while True:
        now = time.monotonic()
        if monitor is not None:
            monitor.check(now)
        effective = min(deadline, monitor.deadline()) if monitor else deadline
        remaining = effective - now
        if remaining <= 0:
            raise TimeoutError
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(idle_timeout, remaining))
        try:
            chunk = response.read1(min(4096, limit - wire + 1))
        except TimeoutError:
            if monitor is not None:
                monitor.check(time.monotonic())
            raise
        wire += len(chunk)
        if monitor is not None:
            monitor.wire = wire
            monitor.check(time.monotonic())
        if wire > limit:
            raise StreamFailure("stream_limit", "wire_limit")
        if time.monotonic() >= deadline:
            raise TimeoutError
        if not chunk:
            return
        yield chunk


def _lines(response, limit, deadline, idle_timeout, monitor):
    pending = b""
    for chunk in chunks(response, limit, deadline, idle_timeout, monitor=monitor):
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if len(line) > 65536:
                raise StreamFailure("stream_limit", "line_limit")
            try:
                yield line.decode("utf-8").rstrip("\r")
            except UnicodeError:
                raise StreamFailure("stream_malformed", "invalid_utf8") from None
        if len(pending) > 65536:
            raise StreamFailure("stream_limit", "line_limit")


class _Progress:
    def __init__(self, idle, first, whitespace, json_mode):
        self.started = self.last_output = self.last_program = time.monotonic()
        self.idle, self.first, self.whitespace, self.json_mode = idle, first, whitespace, json_mode
        self.wire = self.events = self.program_bytes = self.reasoning_bytes = 0
        self.gap = 0
        self.in_string = self.escaped = self.has_program = self.finished = False

    def deadline(self):
        deadlines = [float("inf")]
        if self.idle is not None:
            deadlines.append(self.last_output + self.idle)
        if self.first is not None and not self.has_program and not self.finished:
            deadlines.append(self.started + self.first)
        return min(deadlines)

    def check(self, now):
        if self.first is not None and not self.has_program and not self.finished:
            if now - self.started >= self.first:
                raise StreamFailure("model_no_progress", "first_program_timeout")
        if self.idle is not None and now - self.last_output >= self.idle:
            raise StreamFailure("model_no_progress", "output_idle_timeout")

    def text(self, text, channel):
        now = time.monotonic()
        if channel == "reasoning":
            self.reasoning_bytes += len(text.encode("utf-8"))
            if text.strip():
                self.last_output = now
            return
        self.program_bytes += len(text.encode("utf-8"))
        meaningful = False
        for char in text:
            # Whitespace inside JSON strings is data, including across SSE chunks.
            if self.json_mode and not self.in_string and char in " \t\r\n":
                self.gap += 1
                if self.whitespace is not None and self.gap > self.whitespace:
                    raise StreamFailure("model_no_progress", "json_whitespace_limit")
                continue
            self.gap = 0
            meaningful |= self.in_string or char not in " \t\r\n"
            if self.json_mode:
                if self.escaped:
                    self.escaped = False
                elif self.in_string and char == "\\":
                    self.escaped = True
                elif char == '"':
                    self.in_string = not self.in_string
        if meaningful:
            self.has_program = True
            self.last_output = self.last_program = now

    def counters(self):
        now = time.monotonic()
        return {
            "wire_bytes": self.wire,
            "sse_events": self.events,
            "program_bytes": self.program_bytes,
            "reasoning_bytes": self.reasoning_bytes,
            "json_whitespace_chars": self.gap,
            "finish_seen": self.finished,
            "elapsed_seconds": round(now - self.started, 3),
            "seconds_since_program": round(now - self.last_program, 3),
        }


def read_completion(
    response,
    *,
    limit,
    deadline,
    loads,
    emit,
    idle_timeout=120,
    progress_timeout=None,
    first_program_timeout=None,
    max_json_whitespace=None,
    json_mode=False,
):
    """Require a finish frame and [DONE]; keep hypotheses separate from program text.

    Progress limits bound one model response, not cumulative task work. Reasoning
    counts as activity but cannot indefinitely postpone the first program byte.
    Only an explicitly JSON response enables the lexical whitespace safeguard.
    """
    progress = _Progress(progress_timeout, first_program_timeout, max_json_whitespace, json_mode)
    parts, reasoning, fields = [], [], []
    usage, finish, ident = None, None, None
    saw_choice = False
    try:
        for line in _lines(response, limit, deadline, idle_timeout, progress):
            if line.startswith(":"):
                continue
            if line:
                if line.startswith("data:"):
                    fields.append(line[5:].removeprefix(" "))
                continue
            if not fields:
                continue
            raw, fields = "\n".join(fields), []
            progress.events += 1
            if raw == "[DONE]":
                if not saw_choice or finish is None:
                    raise StreamFailure("stream_interrupted", "missing_finish", retryable=True)
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "".join(parts),
                                "reasoning_content": "".join(reasoning),
                            },
                            "finish_reason": finish,
                        }
                    ],
                    "usage": usage,
                    "id": ident,
                }
            try:
                value = loads(raw)
            except (ValueError, TypeError, RecursionError):
                raise StreamFailure("stream_malformed", "invalid_json") from None
            if not isinstance(value, dict):
                raise StreamFailure("stream_malformed", "invalid_frame")
            if "error" in value:
                error = value["error"]
                code = error.get("code") if isinstance(error, dict) else None
                status = (
                    code
                    if type(code) is int
                    and code in {400, 401, 402, 403, 404, 408, 429, 500, 502, 503, 504}
                    else None
                )
                raise StreamFailure(
                    "stream_upstream_error",
                    "upstream_error",
                    status=status,
                    retryable=status in {408, 429, 500, 502, 503, 504},
                )
            if isinstance(value.get("id"), str):
                ident = value["id"]
            if value.get("usage") is not None:
                if not isinstance(value["usage"], dict):
                    raise StreamFailure("stream_malformed", "invalid_usage")
                usage = value["usage"]
            choices = value.get("choices")
            if not isinstance(choices, list) or len(choices) > 1:
                raise StreamFailure("stream_malformed", "invalid_choices")
            if not choices:
                continue
            choice = choices[0]
            if (
                not isinstance(choice, dict)
                or type(choice.get("index", 0)) is not int
                or choice.get("index", 0) != 0
            ):
                raise StreamFailure("stream_malformed", "invalid_choice")
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                raise StreamFailure("stream_malformed", "invalid_delta")
            if any(delta.get(k) for k in ("tool_calls", "function_call", "refusal")):
                raise StreamFailure("stream_unsupported", "non_program_delta")
            saw_choice = True
            for field, target, channel in (
                ("content", parts, "program"),
                ("reasoning_content", reasoning, "reasoning"),
            ):
                text = delta.get(field)
                if text is not None:
                    if not isinstance(text, str) or (finish is not None and text):
                        raise StreamFailure("stream_malformed", "invalid_text")
                    try:
                        progress.text(text, channel)
                    except UnicodeError:
                        raise StreamFailure("stream_malformed", "invalid_text") from None
                    target.append(text)
                    if text:
                        emit({"kind": "model_delta", "channel": channel, "text": text})
            reason = choice.get("finish_reason")
            if reason is not None:
                if not isinstance(reason, str) or finish is not None:
                    raise StreamFailure("stream_malformed", "invalid_finish")
                finish, progress.finished = reason, True
                progress.last_output = time.monotonic()
        raise StreamFailure("stream_interrupted", "early_eof", retryable=True)
    except StreamFailure as exc:
        exc.diagnostics = {"reason": exc.reason, **progress.counters()}
        raise
    finally:
        emit({"kind": "model_stream_diagnostics", **progress.counters()})
