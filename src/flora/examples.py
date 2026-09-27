"""Executable reference programs and local, stateful tutorial environments.

These are authored IR examples, not pre-recorded model responses or benchmark
results. Every reported tool call goes through the actual runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import parse_program
from .tools import ToolRegistry, ToolSpec
from .trace import GENESIS
from .values import clone


def var(name):
    return {"var": name}


def op(name, dest, *args):
    return {"op": name, "dest": dest, "args": list(args)}


def block(params, ops, term):
    return {"params": params, "ops": ops, "term": term}


def effect(tool, args, resume="done", bind="reply", capture=None):
    return {
        "op": "effect",
        "tool": tool,
        "args": args,
        "resume": resume,
        "bind": bind,
        "capture": capture or {},
    }


def move_program(minutes: int) -> dict:
    return parse_program(
        {
            "version": 1,
            "entry": "main",
            "blocks": {
                "main": block([], [], effect("move_event", {"id": "A", "start_minutes": minutes})),
                "done": block(
                    ["reply"],
                    [op("get", "value", var("reply"), "value")],
                    {"op": "return", "value": var("value")},
                ),
            },
        }
    )


def calendar_diagnostic_program() -> dict:
    return parse_program(
        {
            "version": 1,
            "entry": "main",
            "blocks": {
                "main": block([], [], effect("read_event", {"id": "B"}, "plan")),
                "plan": block(
                    ["reply"],
                    [
                        op("get", "event", var("reply"), "value"),
                        op("get", "start", var("event"), "start_minutes"),
                        op("add", "target", var("start"), 30),
                    ],
                    effect("move_event", {"id": "A", "start_minutes": var("target")}),
                ),
                "done": block(
                    ["reply"],
                    [op("get", "value", var("reply"), "value")],
                    {"op": "return", "value": var("value")},
                ),
            },
        }
    )


def calendar_bundle(epoch: int = 0, trace_digest: str = GENESIS) -> dict:
    return {
        "programs": [
            {"id": "cached", "program": move_program(870), "inputs": {}},
            {"id": "notice", "program": move_program(930), "inputs": {}},
        ],
        "incumbent": "cached",
        "expected_epoch": epoch,
        "expected_digest": trace_digest,
        "diagnostics": [
            {
                "id": "verify_time",
                "program": calendar_diagnostic_program(),
                "inputs": {},
                "forecasts": [
                    {
                        "candidate_id": "cached",
                        "predicate": {"op": "eq", "path": ["start_minutes"], "value": 840},
                    },
                    {
                        "candidate_id": "notice",
                        "predicate": {"op": "eq", "path": ["start_minutes"], "value": 900},
                    },
                ],
                "witnesses": [{"id": "B", "start_minutes": 840}, {"id": "B", "start_minutes": 900}],
            }
        ],
    }


@dataclass
class CalendarWorld:
    events: dict = field(default_factory=lambda: {"A": 960, "B": 900})
    calls: list = field(default_factory=list)

    def read_event(self, id: str):
        self.calls.append({"tool": "read_event", "id": id})
        return {"id": id, "start_minutes": self.events[id]}

    def move_event(self, id: str, start_minutes: int):
        self.calls.append({"tool": "move_event", "id": id, "start_minutes": start_minutes})
        self.events[id] = start_minutes
        return {"id": id, "start_minutes": start_minutes}

    def registry(self):
        return ToolRegistry(
            [
                ToolSpec(
                    "read_event",
                    self.read_event,
                    "Read the currently observed start time in minutes after midnight.",
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string", "enum": ["A", "B"]}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                ),
                ToolSpec(
                    "move_event",
                    self.move_event,
                    "Change an existing event's start time. This mutates the calendar.",
                    {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "enum": ["A", "B"]},
                            "start_minutes": {"type": "integer", "minimum": 0, "maximum": 1439},
                        },
                        "required": ["id", "start_minutes"],
                        "additionalProperties": False,
                    },
                ),
            ]
        )


def calendar_tools() -> ToolRegistry:
    """CLI adapter factory. The host state is private; only two tools are exposed."""
    return CalendarWorld().registry()


def pagination_program(field_name: str) -> dict:
    loop_params = ["rows", "n", "i", "acc", "more"]
    same = {name: var(name) for name in loop_params}
    return parse_program(
        {
            "version": 1,
            "entry": "main",
            "blocks": {
                "main": block([], [], effect("next_page", {}, "consume", capture={"acc": []})),
                "consume": block(
                    ["acc", "reply"],
                    [
                        op("get", "page", var("reply"), "value"),
                        op("get", "rows", var("page"), field_name),
                        op("get", "more", var("page"), "has_more"),
                        op("length", "n", var("rows")),
                    ],
                    {
                        "op": "jump",
                        "target": "loop",
                        "args": {
                            "rows": var("rows"),
                            "n": var("n"),
                            "i": 0,
                            "acc": var("acc"),
                            "more": var("more"),
                        },
                    },
                ),
                "loop": block(
                    loop_params,
                    [op("lt", "continue", var("i"), var("n"))],
                    {
                        "op": "branch",
                        "condition": var("continue"),
                        "yes": "item",
                        "no": "after",
                        "args": same,
                    },
                ),
                "item": block(
                    loop_params,
                    [
                        op("get", "row", var("rows"), var("i")),
                        op("get", "id", var("row"), "id"),
                        op("append", "updated", var("acc"), var("id")),
                        op("add", "next_i", var("i"), 1),
                    ],
                    {
                        "op": "jump",
                        "target": "loop",
                        "args": {**same, "acc": var("updated"), "i": var("next_i")},
                    },
                ),
                "after": block(
                    loop_params,
                    [],
                    {
                        "op": "branch",
                        "condition": var("more"),
                        "yes": "next",
                        "no": "finish",
                        "args": {"acc": var("acc")},
                    },
                ),
                "next": block(
                    ["acc"], [], effect("next_page", {}, "consume", capture={"acc": var("acc")})
                ),
                "finish": block(["acc"], [], {"op": "return", "value": var("acc")}),
            },
        }
    )


def pagination_bundle(epoch: int = 0, trace_digest: str = GENESIS) -> dict:
    return {
        "programs": [
            {"id": "items_parser", "program": pagination_program("items"), "inputs": {}},
            {"id": "data_parser", "program": pagination_program("data"), "inputs": {}},
        ],
        "incumbent": "items_parser",
        "diagnostics": [],
        "expected_epoch": epoch,
        "expected_digest": trace_digest,
    }


@dataclass
class PaginationWorld:
    pages: list = field(
        default_factory=lambda: [
            {"data": [{"id": "a"}, {"id": "b"}], "has_more": True},
            {"data": [{"id": "c"}], "has_more": False},
        ]
    )
    cursor: int = 0

    def next_page(self):
        if self.cursor >= len(self.pages):
            raise IndexError("EndOfStream")
        page = clone(self.pages[self.cursor])
        self.cursor += 1
        return page

    def registry(self):
        return ToolRegistry(
            [
                ToolSpec(
                    "next_page",
                    self.next_page,
                    "Consume and return the next page. Every call advances the cursor; it cannot rewind.",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                )
            ]
        )


def pagination_tools() -> ToolRegistry:
    return PaginationWorld().registry()


def run_demo(name: str, *, trace=None, config=None) -> dict:
    from .runtime import Runtime

    if name == "calendar":
        world = CalendarWorld()
        runtime = Runtime(world.registry(), trace=trace, config=config)
        result = runtime.run(
            "Move event A to 30 minutes after the current start of B. Old receipt says B=840; a later message suggests B=900; current time is unknown.",
            bundle=calendar_bundle(runtime.trace.epoch, runtime.trace.digest),
        )
        observation = {"calls": world.calls, "events": world.events}
    elif name == "pagination":
        world = PaginationWorld()
        runtime = Runtime(world.registry(), trace=trace, config=config)
        result = runtime.run(
            "Consume all pages once and return every record ID.",
            bundle=pagination_bundle(runtime.trace.epoch, runtime.trace.digest),
        )
        observation = {"cursor": world.cursor}
    else:
        raise ValueError("Unknown demo; choose calendar or pagination")
    return {
        "execution": "offline fixture with authored IR; no model calls",
        "result": result.to_dict(),
        "host_observation": observation,
        "trace": runtime.trace.export(),
        "contracts": runtime.contracts.to_dict(),
    }
