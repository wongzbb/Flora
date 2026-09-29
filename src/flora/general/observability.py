# SPDX-License-Identifier: Apache-2.0
"""Actual model/tool dialogue for humans; never an alternate execution ledger."""

from __future__ import annotations

import functools
import inspect
import json
import os
import re
import threading
import time
import uuid
from dataclasses import replace

SECRET_FIELD = re.compile(
    r"api.?key|authorization|password|secret|access.?token|refresh.?token", re.I
)


class Dialogue:
    def __init__(self, store, callback, *, actor="main", secrets=()):
        self.store, self.callback, self.actor = store, callback, actor
        self.secrets = tuple(s for s in secrets if s)
        self.buffers, self.lock = {}, threading.RLock()
        self.request = ""
        self.last_flush = time.monotonic()

    def clean(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[credential redacted]")
        text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[redacted]", text)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[credential redacted]", text)

    def value(self, value, depth=0):
        if depth > 24:
            return "[nested value omitted]"
        if isinstance(value, str):
            return self.clean(value)
        if type(value) in (int, float, bool) or value is None:
            return value
        if isinstance(value, dict):
            return {
                str(k): "[redacted]" if SECRET_FIELD.search(str(k)) else self.value(v, depth + 1)
                for k, v in list(value.items())[:1000]
            }
        if isinstance(value, (list, tuple)):
            return [self.value(v, depth + 1) for v in value[:1000]]
        return "[opaque host value]"  # Never invoke arbitrary __repr__ or serialize a live handle.

    def write(self, channel, text, **metadata):
        text = self.clean(text)
        for start in range(0, max(len(text), 1), 4096):
            event = {
                "kind": "transcript",
                "actor": self.actor,
                "channel": channel,
                "text": text[start : start + 4096],
                "request": self.request,
                **metadata,
            }
            try:
                event["id"] = self.store.transcript_append(event)
                if self.callback:
                    self.callback(event)
            except Exception:
                # Display/storage failure cannot turn a successful external effect into a retry.
                pass

    def flush(self, *, final=False):
        for channel, text in list(self.buffers.items()):
            # Hold any credential prefix that straddles network chunks. This
            # applies before BOTH display and persistence, including failures.
            for secret in self.secrets:
                text = text.replace(secret, "[credential redacted]")
            hold = 0
            for secret in self.secrets:
                for size in range(min(len(secret) - 1, len(text)), 0, -1):
                    if text.endswith(secret[:size]):
                        hold = max(hold, size)
                        break
            match = re.search(r"(?:\bsk-[A-Za-z0-9_-]*|(?i:Bearer)\s+[^\s\"']*)$", text)
            if match:
                hold = max(hold, len(text) - match.start())
            visible = text[:-hold] if hold else text
            if final and hold:
                visible += "[credential fragment redacted]"
            self.buffers[channel] = text[-hold:] if hold and not final else ""
            if visible:
                self.write(channel, visible)
        self.last_flush = time.monotonic()

    def event(self, event):
        with self.lock:
            kind = event.get("kind")
            if kind == "model_request":
                self.flush(final=True)
                self.request = uuid.uuid4().hex[:12]
                self.write(
                    "request",
                    json.dumps(
                        {
                            k: event[k]
                            for k in ("model", "stream", "json_mode", "reasoning_fallback")
                            if k in event
                        },
                        ensure_ascii=False,
                    ),
                )
                for message in event["messages"]:
                    self.write("prompt/" + message["role"], message["content"])
            elif kind == "model_delta":
                channel = event["channel"]
                self.buffers[channel] = self.buffers.get(channel, "") + event["text"]
                if len(self.buffers[channel]) >= 160 or time.monotonic() - self.last_flush >= 0.2:
                    self.flush()
            else:
                self.flush(final=True)
                self.write(kind or "event", json.dumps(self.value(event), ensure_ascii=False))

    def tools(self, specs):
        def wrap(spec):
            def start(kwargs):
                try:
                    text = json.dumps(self.value(kwargs), ensure_ascii=False)
                    self.write(
                        "tool/call",
                        text[:24000] + ("\n[display truncated]" if len(text) > 24000 else ""),
                        tool=spec.name,
                    )
                except Exception:
                    pass

            def done(value):
                text = json.dumps(self.value(value), ensure_ascii=False, allow_nan=False)
                # The human log is bounded independently of the authoritative receipt.
                self.write(
                    "tool/result",
                    text[:24000] + ("\n[display truncated]" if len(text) > 24000 else ""),
                    tool=spec.name,
                )
                return value

            def error(exc):
                try:
                    self.write(
                        "tool/error",
                        type(exc).__name__ + ": " + self.clean(str(exc))[:4000],
                        tool=spec.name,
                    )
                except Exception:
                    pass

            @functools.wraps(spec.handler)
            def sync(**kwargs):
                start(kwargs)
                try:
                    value = spec.handler(**kwargs)
                except BaseException as exc:
                    error(exc)
                    raise
                try:
                    done(value)
                except Exception:
                    pass
                return value

            @functools.wraps(spec.handler)
            async def asynchronous(**kwargs):
                start(kwargs)
                try:
                    value = await spec.handler(**kwargs)
                except BaseException as exc:
                    error(exc)
                    raise
                try:
                    done(value)
                except Exception:
                    pass
                return value

            return replace(
                spec, handler=asynchronous if inspect.iscoroutinefunction(spec.handler) else sync
            )

        return [wrap(spec) for spec in specs]

    def close(self):
        with self.lock:
            self.flush(final=True)
            self.buffers.clear()
            self.secrets = ()
            self.callback = None


def connect(agent, dialogue, profile):
    """Enable application transport policy without rewriting an existing session identity.

    Endpoint/model, capabilities, execution anchors and remaining budgets remain
    pinned. These preferences affect delivery/visibility, never effect semantics.
    Explicit stream/JSON options in the saved profile take precedence.
    """
    from flora.integrations.providers import OpenAICompatibleProvider

    if isinstance(agent.provider, OpenAICompatibleProvider):
        agent.provider.stream = profile.get("provider", {}).get("stream", True)
        agent.provider.prefer_json = True
        agent.provider.on_event = dialogue.event
        key = agent.provider._session_key or os.environ.get(agent.provider.api_key_env or "", "")
        dialogue.secrets = tuple(set(dialogue.secrets + ((key,) if key else ())))
    agent.compiler.transport_retries = 2
    agent.compiler.on_event = dialogue.event
