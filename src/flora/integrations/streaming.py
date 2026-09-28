# SPDX-License-Identifier: Apache-2.0
"""Bounded SSE decoding. A partial stream is never a completed compiler response."""

from __future__ import annotations

import time


def chunks(response, limit, deadline, idle_timeout):
    wire = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        # CPython HTTPResponse exposes the socket through its buffered reader.
        # The deadline is also checked around each read for alternate readers.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(idle_timeout, remaining))
        chunk = response.read1(min(4096, limit - wire + 1))
        wire += len(chunk)
        if wire > limit:
            raise ValueError("stream byte limit")
        if time.monotonic() >= deadline:
            raise TimeoutError
        if not chunk:
            return
        yield chunk


def _lines(response, limit, deadline, idle_timeout):
    pending = b""
    for chunk in chunks(response, limit, deadline, idle_timeout):
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if len(line) > 65536:
                raise ValueError("stream line limit")
            yield line.decode("utf-8").rstrip("\r")
        if len(pending) > 65536:
            raise ValueError("stream line limit")


def read_completion(response, *, limit, deadline, loads, emit, idle_timeout=120):
    """Read one choice through [DONE], retaining usage and exposing received deltas.

    A finish_reason alone is insufficient: the final usage frame may follow it.
    Comments/keep-alives do not count as model output. Wire bytes (including
    comments) and elapsed time are bounded, including an unterminated SSE line.
    """
    parts, reasoning, fields = [], [], []
    usage, finish, ident = None, None, None
    saw_choice = False
    for line in _lines(response, limit, deadline, idle_timeout):
        if line.startswith(":"):
            continue
        if line:
            if line.startswith("data:"):
                fields.append(line[5:].removeprefix(" "))
            continue
        if not fields:
            continue
        raw, fields = "\n".join(fields), []
        if raw == "[DONE]":
            if not saw_choice or finish is None:
                raise EOFError("missing finish frame")
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
        value = loads(raw)
        if not isinstance(value, dict) or "error" in value:
            raise ValueError("invalid stream frame")
        if isinstance(value.get("id"), str):
            ident = value["id"]
        if value.get("usage") is not None:
            if not isinstance(value["usage"], dict):
                raise ValueError("invalid stream usage")
            usage = value["usage"]
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("one streaming choice required")
        if not choices:  # Optional usage-only frame.
            continue
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
            raise ValueError("invalid streaming choice")
        delta = choice.get("delta")
        if not isinstance(delta, dict) or any(
            delta.get(k) for k in ("tool_calls", "function_call", "refusal")
        ):
            raise ValueError("unsupported streaming delta")
        saw_choice = True
        for field, target, channel in (
            ("content", parts, "program"),
            ("reasoning_content", reasoning, "reasoning"),
        ):
            text = delta.get(field)
            if text is not None:
                if not isinstance(text, str) or (finish is not None and text):
                    raise ValueError("invalid streaming text")
                target.append(text)
                if text:
                    emit({"kind": "model_delta", "channel": channel, "text": text})
        reason = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str) or finish is not None:
                raise ValueError("invalid finish frame")
            finish = reason
    raise EOFError("incomplete stream")
