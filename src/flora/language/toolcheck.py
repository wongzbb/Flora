# SPDX-License-Identifier: Apache-2.0
"""Conservative, capability-agnostic checks of model-authored effect arguments.

This is a source compiler gate, not an interpreter or a task-success oracle.
Only statically known contradictions of the host's advertised schema are rejected.
Computed/observed values (including opaque handles) remain runtime obligations.
No tool, pure operation, branch, migration, or speculative candidate is executed.
"""

from __future__ import annotations

from flora.integrations.tools import validate_schema
from flora.support.errors import ValidationError

_UNKNOWN = object()


def _abstract(expression):
    if isinstance(expression, dict):
        if set(expression) == {"var"}:
            return _UNKNOWN
        if set(expression) == {"literal"}:
            return expression["literal"]  # Escaped data is not recursively interpreted.
        return {key: _abstract(value) for key, value in expression.items()}
    if isinstance(expression, list):
        return [_abstract(value) for value in expression]
    return expression


def _known(value):
    if value is _UNKNOWN:
        return False
    if isinstance(value, dict):
        return all(_known(item) for item in value.values())
    if isinstance(value, list):
        return all(_known(item) for item in value)
    return True


def _check(value, schema, path, depth=0):
    if depth > 64 or not isinstance(schema, dict):
        raise ValidationError("Invalid or excessively nested tool schema")
    if value is _UNKNOWN:
        return
    if _known(value):
        validate_schema(value, schema, path, depth=depth)
        return
    # The container's type/keys/length are known; its descendant values are not.
    # Reuse the runtime validator's keyword semantics, but don't mistake source
    # variable expressions for the actual data they will eventually represent.
    shallow = dict(schema)
    shallow.pop("enum", None)  # Membership cannot be established for partial values.
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValidationError("Invalid object schema")
        shallow["properties"] = {key: {} for key in properties}
        additional = schema.get("additionalProperties", {})
        if isinstance(additional, dict):
            shallow["additionalProperties"] = {}
        validate_schema(value, shallow, path, depth=depth)
        for key, item in value.items():
            child_schema = properties.get(key, additional)
            if isinstance(child_schema, dict):
                _check(item, child_schema, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        shallow["items"] = {}
        validate_schema(value, shallow, path, depth=depth)
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{i}]", depth + 1)


def validate_effect_arguments(bundle, tools):
    """Check every candidate/diagnostic/revision using only its granted tool schema.

    Called after ordinary IR validation and never changes the supplied program.
    Acceptance says nothing about dynamic values, tool success, or task correctness.
    """
    specifications = {tool["name"]: tool for tool in tools}
    errors = []
    for kind in ("programs", "diagnostics", "revisions"):
        for item in bundle.get(kind, []):
            for label, block in item["program"]["blocks"].items():
                term = block["term"]
                if term["op"] != "effect":
                    continue
                spec = specifications.get(term["tool"], {})
                schema = spec.get("parameters")
                if schema is None:
                    continue
                # Opaque carriers have a runtime-specific schema and live-store
                # identity check. A declared opaque slot cannot be typed from JSON.
                value = _abstract(term["args"])
                if isinstance(value, dict):
                    value = dict(value)
                    for name in spec.get("opaque_parameters", []):
                        if name in value:
                            value[name] = _UNKNOWN
                try:
                    _check(value, schema, "args")
                except ValidationError as exc:
                    errors.append(f"{kind} {item['id']}, block {label}, tool {term['tool']}: {exc}")
                    if len(errors) == 16:
                        raise ValidationError("\n".join(errors)) from None
    if errors:
        raise ValidationError("\n".join(errors))
