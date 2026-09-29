# SPDX-License-Identifier: Apache-2.0
import io
import json

from flora.language.compiler import CompilerContext
from flora.state.trace import GENESIS


def block(params=(), ops=(), term=None):
    return {"params": list(params), "ops": list(ops), "term": term}


def pure(value, params=()):
    return {
        "version": 1,
        "entry": "main",
        "blocks": {"main": block(params, term={"op": "return", "value": value})},
    }


def bundle(program=None, epoch=0, trace_digest=GENESIS):
    return {
        "programs": [{"id": "main", "program": program or pure(42), "inputs": {}}],
        "incumbent": "main",
        "diagnostics": [],
        "expected_epoch": epoch,
        "expected_digest": trace_digest,
    }


def context(tools=()):
    return CompilerContext("fixture", list(tools), 0, GENESIS)


def frame(content=None, *, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def sse(*frames):
    return b"".join(
        b"data: " + (f.encode() if isinstance(f, str) else json.dumps(f).encode()) + b"\n\n"
        for f in frames
    )


class Response(io.BytesIO):
    def __init__(self, body, content_type="text/event-stream"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
