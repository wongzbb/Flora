# SPDX-License-Identifier: Apache-2.0
"""Public-protocol bridge for an already prepared AgentEnv-like environment.

This module imports neither JAZ nor an evaluation framework. The caller owns
environment setup, lifetime, isolation and scoring. Only published tool bindings
cross into the registry; no environment inspection, reset or grader is added.

Callable tools keep their names and handlers. A published object/API tree uses
the same name with ``path`` and ``kwargs`` transport arguments. This changes the
invocation syntax and must be reported in a benchmark comparison. A bridge is
not evidence that a benchmark has been run or that its score is preserved.
"""

from __future__ import annotations

import inspect
import keyword
from collections.abc import Mapping
from typing import Any

from flora.integrations.tools import ToolRegistry, ToolSpec
from flora.support.errors import ValidationError
from flora.support.values import canonical_json, clone

_FRAMEWORK_NAMES = {
    "grade",
    "setup",
    "close",
    "get_instructions",
    "get_single_task_instructions",
    "is_complete",
    "delivered_task_tool",
    "tools",
    "tool_bindings",
    "shared_tool_bindings",
    "root_tool_bindings",
    "root_only_tool_names",
    "set_artifacts_dir",
    "set_isolation",
    "isolation_key",
    "analyze_run",
    "actual_tool_calls",
}
_TREE_SCHEMA = {
    "type": "object",
    "required": ["path", "kwargs"],
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {"type": "string", "minLength": 1},
        },
        "kwargs": {"type": "object"},
    },
}


def _public_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.isidentifier()
        and not value.startswith("_")
        and not keyword.iskeyword(value)
    )


def _method(env: Any, name: str):
    try:
        method = getattr(env, name)
    except AttributeError:
        raise ValidationError(f"AgentEnv public protocol is missing {name}") from None
    if not callable(method):
        raise ValidationError(f"AgentEnv public protocol {name} must be callable")
    return method


def _bindings(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("AgentEnv bindings must be a mapping")
    result = dict(value)
    if any(not _public_identifier(name) or name in _FRAMEWORK_NAMES for name in result):
        raise ValidationError("AgentEnv bindings contain a private or framework attribute")
    return result


def _card(value: Any) -> dict[str, str]:
    try:
        fields = {
            name: value[name] if isinstance(value, Mapping) else getattr(value, name)
            for name in ("name", "signature", "description")
        }
    except (KeyError, AttributeError):
        raise ValidationError("AgentEnv tool cards need name, signature and description") from None
    if any(not isinstance(item, str) for item in fields.values()):
        raise ValidationError("AgentEnv tool card fields must be strings")
    if not _public_identifier(fields["name"]):
        raise ValidationError("AgentEnv tool names must be public identifiers")
    return fields


def _callable_schema(handler) -> dict:
    """Expose parameter names without evaluating annotations or inventing types."""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return {"type": "object"}
    properties, required = {}, []
    extra = False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            if parameter.default is inspect.Parameter.empty:
                raise ValidationError(
                    "AgentEnv bridge cannot transport required positional-only parameters"
                )
        elif parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            properties[parameter.name] = {}
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter.name)
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            extra = True
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": extra,
    }


def _tree_transport(tree: Any, name: str):
    def invoke(path: list[str], kwargs: dict) -> Any:
        if (
            type(path) is not list
            or not 1 <= len(path) <= 32
            or any(not _public_identifier(segment) for segment in path)
        ):
            raise ValidationError("API path must contain 1..32 public non-underscore identifiers")
        if type(kwargs) is not dict:
            raise ValidationError("API kwargs must be a JSON object")
        canonical_json(kwargs)
        endpoint = tree
        for segment in path:
            # Do not use object.__getattribute__, enumerate endpoints, or bypass
            # a host tree's own authorization and hidden-endpoint checks.
            endpoint = getattr(endpoint, segment)
        if not callable(endpoint):
            raise ValidationError("The selected public API path is not callable")
        return endpoint(**kwargs)

    invoke.__name__ = name
    invoke.__qualname__ = name
    invoke.__doc__ = "Invoke one existing public API path using keyword arguments."
    return invoke


class AgentEnvBridge:
    """Adapt the public AgentEnv protocol without importing that framework.

    ``env`` must already be prepared by its owning evaluation runner. Pull queues
    keep their published fetch/submit tools. Push delivery is explicitly rejected
    before any tool bindings are read or task is fetched.

    ``registry`` contains shared bindings plus root-only bindings when ``root``
    is true. ``instructions`` is the original environment text. The root's
    ``completion_guard`` reads only public ``is_complete`` and requires a bool;
    it is ``None`` for a subordinate session, which must not finish the entire
    queue. The guard is lifecycle bookkeeping, never a correctness oracle.
    """

    def __init__(self, env: Any, root: bool = True) -> None:
        if type(root) is not bool:
            raise ValidationError("AgentEnv bridge root must be boolean")
        delivery = _method(env, "delivered_task_tool")()
        if delivery is not None:
            raise ValidationError(
                "Push task delivery is not supported by AgentEnvBridge; provide an explicit delivery adapter"
            )
        bindings = _bindings(_method(env, "shared_tool_bindings")())
        if root:
            root_bindings = _bindings(_method(env, "root_tool_bindings")())
            if set(bindings) & set(root_bindings):
                raise ValidationError("AgentEnv shared and root binding names overlap")
            bindings.update(root_bindings)
        try:
            published = env.tools
        except AttributeError:
            raise ValidationError("AgentEnv public protocol is missing tools") from None
        if not isinstance(published, (list, tuple)):
            raise ValidationError("AgentEnv tools must be a public list of tool cards")
        cards = [_card(item) for item in published]
        indexed = {card["name"]: card for card in cards}
        if len(indexed) != len(cards) or set(bindings) - set(indexed):
            raise ValidationError(
                "AgentEnv tool cards are duplicated or bindings lack public descriptions"
            )
        instructions = _method(env, "get_instructions")()
        if not isinstance(instructions, str):
            raise ValidationError("AgentEnv instructions must be a string")
        completion = _method(env, "is_complete") if root else None
        specs = []
        # Published ordering is retained at registration; the core registry's
        # descriptions use its normal deterministic name ordering.
        for card in cards:
            name = card["name"]
            if name not in bindings:
                continue
            handler = bindings[name]
            description = f"{name}{card['signature']}\n{card['description']}"
            # AppWorld's public API proxy is callable, but publishes an empty
            # signature to identify an object tree rather than a root function.
            if not callable(handler) or card["signature"] == "":
                handler = _tree_transport(handler, name)
                description += (
                    "\nFlora transport: invoke this same tool with "
                    "{path: ['app', 'endpoint'], kwargs: {...}} for "
                    f"{name}.app.endpoint(**kwargs). Only existing public "
                    "non-underscore attribute paths are allowed. Missing or "
                    "unavailable paths raise; they are not empty observations."
                )
                schema = _TREE_SCHEMA
            else:
                schema = _callable_schema(handler)
            specs.append(
                ToolSpec(name, handler, description=description, input_schema=clone(schema))
            )

        self.registry = ToolRegistry(specs)
        self.instructions = instructions
        self.root = root
        self.completion_guard = None
        if completion is not None:

            def guard() -> bool:
                result = completion()
                if type(result) is not bool:
                    raise ValidationError("AgentEnv is_complete must return an actual boolean")
                return result

            self.completion_guard = guard
