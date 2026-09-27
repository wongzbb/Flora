# SPDX-License-Identifier: Apache-2.0
"""Explicit trusted host capabilities. Generated IR cannot register new tools."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from flora.state.opaque import OPAQUE_KEY, OpaqueStore, contains_opaque, is_opaque
from flora.support.errors import ValidationError
from flora.support.values import canonical_json, clone

_OPAQUE_SCHEMA = {
    "type": "object",
    "required": [OPAQUE_KEY],
    "additionalProperties": False,
    "properties": {
        OPAQUE_KEY: {
            "type": "object",
            "required": ["token", "type"],
            "additionalProperties": False,
            "properties": {
                "token": {"type": "string", "minLength": 1},
                "type": {"type": "string", "minLength": 1},
            },
        }
    },
}


def validate_schema(value: Any, schema: dict, path: str = "$", *, depth: int = 0) -> None:
    """Validate the deliberately documented JSON Schema subset used for tools.

    Supported keywords: type, properties, required, additionalProperties,
    items, enum, minimum, maximum, minLength/maxLength, minItems/maxItems,
    description, title, default. Unsupported constraint keywords fail closed.
    """
    if depth > 64 or not isinstance(schema, dict):
        raise ValidationError("Invalid or excessively nested tool schema")
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "description",
        "title",
        "default",
    }
    if set(schema) - allowed:
        raise ValidationError(
            f"Unsupported schema keyword at {path}: {sorted(set(schema) - allowed)}"
        )
    typ = schema.get("type")
    checks = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    types = typ if isinstance(typ, list) else [typ] if typ is not None else []
    if any(t not in checks for t in types) or (types and not any(checks[t] for t in types)):
        raise ValidationError(f"Tool argument type mismatch at {path}")
    if "enum" in schema and not any(
        canonical_json(value) == canonical_json(x) for x in schema["enum"]
    ):
        raise ValidationError(f"Tool argument outside enum at {path}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(props, dict)
            or not isinstance(required, list)
            or not all(isinstance(k, str) for k in required)
        ):
            raise ValidationError("Invalid object schema")
        for key in required:
            if key not in value:
                raise ValidationError(f"Required tool argument missing: {path}.{key}")
        for key, item in value.items():
            if key in props:
                validate_schema(item, props[key], f"{path}.{key}", depth=depth + 1)
            elif schema.get("additionalProperties") is False:
                raise ValidationError(f"Unexpected tool argument: {path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    item, schema["additionalProperties"], f"{path}.{key}", depth=depth + 1
                )
    if isinstance(value, list):
        if "items" in schema:
            for i, item in enumerate(value):
                validate_schema(item, schema["items"], f"{path}[{i}]", depth=depth + 1)
        for key, comparator in (
            ("minItems", lambda a, b: a >= b),
            ("maxItems", lambda a, b: a <= b),
        ):
            if key in schema and not comparator(len(value), schema[key]):
                raise ValidationError(f"Tool array length mismatch at {path}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get(
            "maxLength", float("inf")
        ):
            raise ValidationError(f"Tool string length mismatch at {path}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -float("inf")) or value > schema.get(
            "maximum", float("inf")
        ):
            raise ValidationError(f"Tool numeric bounds mismatch at {path}")


@dataclass(frozen=True)
class ToolSpec:
    """One trusted host capability and its opt-in opaque argument positions.

    ``opaque_parameters`` names top-level keyword arguments allowed to receive a
    complete authenticated carrier. This is explicit host authorization, never
    inferred from schema type or a generated program. Ordinary values in these
    positions still obey the original JSON schema. Nested carriers are rejected.
    """

    name: str
    handler: Callable[..., Any]
    description: str = ""
    input_schema: dict | None = None
    timeout_seconds: float | None = None
    opaque_parameters: tuple[str, ...] = ()

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", self.name) or not callable(
            self.handler
        ):
            raise ValidationError("Invalid tool registration")
        if self.timeout_seconds is not None:
            if self.timeout_seconds <= 0 or not self.timeout_seconds < float("inf"):
                raise ValidationError("Invalid tool timeout")
            if not inspect.iscoroutinefunction(self.handler):
                raise ValidationError(
                    "Runtime timeouts require async tools; synchronous adapters must bound their own I/O"
                )
        if self.input_schema is not None:
            canonical_json(self.input_schema)
        if (
            type(self.opaque_parameters) is not tuple
            or any(type(name) is not str or not name for name in self.opaque_parameters)
            or len(set(self.opaque_parameters)) != len(self.opaque_parameters)
        ):
            raise ValidationError("opaque_parameters must be a tuple of unique argument names")


class ToolRegistry:
    """Explicit tools plus a process-local store for non-JSON result references.

    Persistence records only carriers, not their live objects. Restoring a trace
    with a different registry/store cannot reconstitute those objects; callers
    must reacquire them through actual tools or stop. No automatic reacquisition
    or retry occurs here.
    """

    def __init__(
        self, tools: list[ToolSpec] | None = None, *, opaque_store: OpaqueStore | None = None
    ) -> None:
        self._tools: dict[str, ToolSpec] = {}
        if opaque_store is not None and not isinstance(opaque_store, OpaqueStore):
            raise ValidationError("opaque_store must be an OpaqueStore")
        self.opaque_store = opaque_store if opaque_store is not None else OpaqueStore()
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValidationError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def descriptions(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": clone(t.input_schema or {"type": "object"}),
                **({"opaque_parameters": list(t.opaque_parameters)} if t.opaque_parameters else {}),
            }
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def validate_request(self, request: dict) -> None:
        if not isinstance(request, dict) or set(request) != {"tool", "args"}:
            raise ValidationError("Effect request needs tool and args")
        if (
            not isinstance(request["tool"], str)
            or request["tool"] not in self._tools
            or not isinstance(request["args"], dict)
        ):
            raise ValidationError("Unknown tool or invalid tool arguments")
        canonical_json(request["args"])
        spec = self._tools[request["tool"]]
        opaque_names = []
        for name, value in request["args"].items():
            if not contains_opaque(value):
                continue
            if name not in spec.opaque_parameters or not is_opaque(value):
                raise ValidationError(
                    "opaque handles require an explicitly allowed top-level argument"
                )
            self.opaque_store.resolve(value)
            opaque_names.append(name)
        if spec.input_schema is not None:
            schema = clone(spec.input_schema)
            if opaque_names:
                if not isinstance(schema, dict) or not isinstance(
                    schema.get("properties", {}), dict
                ):
                    raise ValidationError("invalid tool object schema")
                properties = schema.setdefault("properties", {})
                for name in opaque_names:
                    properties[name] = clone(_OPAQUE_SCHEMA)
            validate_schema(request["args"], schema)
        try:
            inspect.signature(spec.handler).bind(**request["args"])
        except ValueError:
            pass  # Builtins may lack signatures; host still controls capability.
        except TypeError as exc:
            raise ValidationError(f"Tool call signature mismatch for {spec.name}: {exc}") from exc

    def encode_result(self, value: Any) -> Any:
        """Make a tool result recordable without introspecting non-JSON objects."""
        return self.opaque_store.encode_result(value)

    def materialize_args(self, request: dict) -> dict:
        """Validate, copy ordinary JSON, and resolve only authorized live handles.

        Resolved objects are never deep-copied. An object can mutate between tool
        calls; its token identifies the object, not a stable state snapshot.
        """
        self.validate_request(request)
        materialized = clone(request["args"])
        for name, value in materialized.items():
            if is_opaque(value):
                materialized[name] = self.opaque_store.resolve(value)
        return materialized

    def call(self, request: dict) -> Any:
        arguments = self.materialize_args(request)
        spec = self._tools[request["tool"]]
        if inspect.iscoroutinefunction(spec.handler):

            async def invoke():
                awaitable = spec.handler(**arguments)
                return (
                    await asyncio.wait_for(awaitable, spec.timeout_seconds)
                    if spec.timeout_seconds
                    else await awaitable
                )

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(invoke())
            raise ValidationError(
                "Run synchronous Flora in a worker thread when an asyncio loop is active"
            )
        result = spec.handler(**arguments)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ValidationError("Sync tool returned awaitable; register an async function")
        return result
