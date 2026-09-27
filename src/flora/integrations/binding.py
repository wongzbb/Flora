# SPDX-License-Identifier: Apache-2.0
"""Safe, convenient binding of ordinary Python functions as agent tools.

Annotations describe the supported JSON boundary; they are never evaluated.
Unknown annotations produce an unconstrained JSON property with an explanatory
note. Python defaults are applied by Python, and are not exposed to the model.
"""

from __future__ import annotations

import ast
import inspect
import math
import types
import typing
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from flora.integrations.tools import ToolRegistry, ToolSpec
from flora.support.errors import ValidationError
from flora.support.values import canonical_json, clone

_UNKNOWN = "Annotation could not be inferred; accepts JSON values. Supply input_schema for stricter validation."
_TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}
_SCHEMA_KEYS = {
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
_PRIMITIVES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "None": "null",
    "NoneType": "null",
}


def _unknown() -> dict:
    return {"description": _UNKNOWN}


def _nullable(schema: dict) -> dict:
    result = clone(schema)
    if "type" in result:
        kinds = result["type"] if isinstance(result["type"], list) else [result["type"]]
        result["type"] = list(dict.fromkeys([*kinds, "null"]))
    if "enum" in result and None not in result["enum"]:
        result["enum"].append(None)
    return result


def _union(schemas: list[dict]) -> dict:
    nonnull = [item for item in schemas if item != {"type": "null"}]
    if len(nonnull) == 1 and len(nonnull) < len(schemas):
        return _nullable(nonnull[0])
    if schemas and all(set(item) == {"type"} for item in schemas):
        kinds = []
        for item in schemas:
            kinds.extend(item["type"] if isinstance(item["type"], list) else [item["type"]])
        return {"type": list(dict.fromkeys(kinds))}
    if schemas and all(item == schemas[0] for item in schemas):
        return schemas[0]
    return _unknown()


def _literal(values: list[Any]) -> dict:
    if not values or any(
        type(value) not in (str, int, float, bool, type(None)) for value in values
    ):
        return _unknown()
    try:
        canonical_json(values)
    except ValidationError:
        return _unknown()
    return {"enum": list({canonical_json(value): value for value in values}.values())}


def _ast_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("typing", "builtins")
    ):
        return node.attr
    return None


def _ast_annotation(node: ast.AST, depth: int = 0) -> dict:
    if depth > 24:
        return _unknown()
    name = _ast_name(node)
    if name in _PRIMITIVES:
        return {"type": _PRIMITIVES[name]}
    if name in ("Any", "object"):
        return {}
    if name in ("list", "List"):
        return {"type": "array"}
    if name in ("dict", "Dict"):
        return {"type": "object"}
    if isinstance(node, ast.Constant):
        if node.value is None:
            return {"type": "null"}
        if isinstance(node.value, str):
            return _string_annotation(node.value, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _union(
            [_ast_annotation(node.left, depth + 1), _ast_annotation(node.right, depth + 1)]
        )
    if isinstance(node, ast.Subscript):
        name = _ast_name(node.value)
        args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if name in ("list", "List") and len(args) == 1:
            return {"type": "array", "items": _ast_annotation(args[0], depth + 1)}
        if name in ("dict", "Dict") and len(args) == 2 and _ast_name(args[0]) == "str":
            return {"type": "object", "additionalProperties": _ast_annotation(args[1], depth + 1)}
        if name == "Optional" and len(args) == 1:
            return _nullable(_ast_annotation(args[0], depth + 1))
        if name == "Union":
            return _union([_ast_annotation(item, depth + 1) for item in args])
        if name == "Literal":
            # literal_eval accepts constants only; calls, attributes and indexing
            # are rejected and are never executed.
            try:
                return _literal([ast.literal_eval(item) for item in args])
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                return _unknown()
    return _unknown()


def _string_annotation(annotation: str, depth: int = 0) -> dict:
    if len(annotation) > 8192 or depth > 24:
        return _unknown()
    try:
        node = ast.parse(annotation, mode="eval").body
        if sum(1 for _ in ast.walk(node)) > 512:
            return _unknown()
        return _ast_annotation(node, depth)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return _unknown()


def _annotation_schema(annotation: Any, depth: int = 0) -> dict:
    if depth > 24:
        return _unknown()
    if annotation is inspect.Parameter.empty or annotation is Any or annotation is object:
        return {}
    for python_type, json_type in (
        (str, "string"),
        (int, "integer"),
        (float, "number"),
        (bool, "boolean"),
        (type(None), "null"),
    ):
        if annotation is python_type:
            return {"type": json_type}
    if annotation is None:
        return {"type": "null"}
    if isinstance(annotation, str):
        return _string_annotation(annotation)
    if isinstance(annotation, typing.ForwardRef):
        return _string_annotation(annotation.__forward_arg__)
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if annotation is list or origin is list:
        return {
            "type": "array",
            **({"items": _annotation_schema(args[0], depth + 1)} if len(args) == 1 else {}),
        }
    if annotation is dict or origin is dict:
        if args and (len(args) != 2 or args[0] is not str):
            return _unknown()
        return {
            "type": "object",
            **({"additionalProperties": _annotation_schema(args[1], depth + 1)} if args else {}),
        }
    if origin is typing.Union or origin is types.UnionType:
        return _union([_annotation_schema(item, depth + 1) for item in args])
    if origin is typing.Literal:
        return _literal(list(args))
    return _unknown()


def _check_schema(schema: dict, depth: int = 0) -> None:
    """Reject malformed or unsupported explicit schemas before any execution."""
    if depth > 32 or type(schema) is not dict or set(schema) - _SCHEMA_KEYS:
        raise ValidationError("Invalid or unsupported tool input_schema")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if (
            not kinds
            or any(type(kind) is not str or kind not in _TYPES for kind in kinds)
            or len(set(kinds)) != len(kinds)
        ):
            raise ValidationError("Invalid schema type")
    for key in ("description", "title"):
        if key in schema and not isinstance(schema[key], str):
            raise ValidationError(f"Schema {key} must be text")
    properties = schema.get("properties", {})
    if type(properties) is not dict or any(type(name) is not str for name in properties):
        raise ValidationError("Invalid schema properties")
    for value in properties.values():
        _check_schema(value, depth + 1)
    required = schema.get("required", [])
    if (
        type(required) is not list
        or any(type(name) is not str for name in required)
        or len(set(required)) != len(required)
    ):
        raise ValidationError("Invalid schema required list")
    if any(name not in properties for name in required):
        raise ValidationError("Required schema fields must be declared in properties")
    if "items" in schema:
        _check_schema(schema["items"], depth + 1)
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if type(additional) is not bool:
            _check_schema(additional, depth + 1)
    for low, high in (("minLength", "maxLength"), ("minItems", "maxItems"), ("minimum", "maximum")):
        for key in (low, high):
            if key in schema:
                value = schema[key]
                if low == "minimum":
                    valid = type(value) in (int, float)
                else:
                    valid = type(value) is int and value >= 0
                if not valid:
                    raise ValidationError(f"Invalid schema {key}")
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ValidationError("Schema lower bound exceeds upper bound")
    if "enum" in schema and (type(schema["enum"]) is not list or not schema["enum"]):
        raise ValidationError("Schema enum must be a nonempty array")


def tool(
    function=None,
    *,
    name=None,
    description=None,
    input_schema=None,
    opaque_parameters=(),
    timeout_seconds=None,
):
    """Bind a function directly or as ``@tool`` / ``@tool(name="search")``.

    Functions are invoked with keyword arguments. Required positional-only
    arguments and ``*args`` are unsupported; optional positional-only arguments
    keep their Python defaults and cannot be supplied. ``**kwargs`` requires an
    explicit object ``input_schema``. No annotation expression is evaluated and
    no callable is invoked during binding. Unsupported annotations accept JSON
    values and are identified in the generated parameter description.
    """
    if function is None:
        return lambda fn: tool(
            fn,
            name=name,
            description=description,
            input_schema=input_schema,
            opaque_parameters=opaque_parameters,
            timeout_seconds=timeout_seconds,
        )
    if isinstance(function, ToolSpec):
        raise ValidationError(
            "tool() expects a callable; use make_registry() for existing ToolSpec values"
        )
    if not callable(function):
        raise ValidationError("Tool must be callable")
    try:
        signature = inspect.signature(function, eval_str=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "Tool requires an inspectable signature; use an explicit ToolSpec adapter"
        ) from exc
    keyword_parameters = {}
    required = []
    has_kwargs = False
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise ValidationError("Tool *args parameters are unsupported; use named parameters")
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            has_kwargs = True
            continue
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            if parameter.default is inspect.Parameter.empty:
                raise ValidationError(
                    "Required positional-only tool arguments are unsupported; add a keyword adapter"
                )
            continue
        keyword_parameters[parameter.name] = parameter
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
    if input_schema is None:
        if has_kwargs:
            raise ValidationError("Tools with **kwargs require an explicit object input_schema")
        schema = {
            "type": "object",
            "properties": {
                key: _annotation_schema(parameter.annotation)
                for key, parameter in keyword_parameters.items()
            },
            "required": required,
            "additionalProperties": False,
        }
    else:
        canonical_json(input_schema)
        schema = clone(input_schema)
        _check_schema(schema)
        if schema.get("type") != "object":
            raise ValidationError("Tool input_schema must have type object")
        properties = schema.get("properties", {})
        if any(key not in properties or key not in schema.get("required", []) for key in required):
            raise ValidationError(
                "Explicit tool schema must declare all required Python parameters"
            )
        if not has_kwargs:
            if any(key not in keyword_parameters for key in properties):
                raise ValidationError(
                    "Tool schema declares an argument absent from the Python signature"
                )
            if schema.get("additionalProperties") not in (None, False):
                raise ValidationError("additionalProperties requires a **kwargs Python parameter")
            schema["additionalProperties"] = False
    if type(opaque_parameters) is not tuple or any(
        type(key) is not str or key not in keyword_parameters and not has_kwargs
        for key in opaque_parameters
    ):
        raise ValidationError("opaque_parameters must name supported keyword arguments")
    if timeout_seconds is not None and (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValidationError("timeout_seconds must be a finite positive number")
    if description is not None and not isinstance(description, str):
        raise ValidationError("Tool description must be text")
    tool_name = name if name is not None else getattr(function, "__name__", type(function).__name__)
    if not isinstance(tool_name, str):
        raise ValidationError("Tool name must be text")
    return ToolSpec(
        name=tool_name,
        handler=function,
        description=description if description is not None else (inspect.getdoc(function) or ""),
        input_schema=schema,
        opaque_parameters=opaque_parameters,
        timeout_seconds=timeout_seconds,
    )


def make_registry(tools=None) -> ToolRegistry:
    """Normalize a registry, iterable of tools, or name-to-tool mapping.

    Existing registries retain identity and their live opaque object store.
    Mapping keys explicitly override names. Duplicate names are rejected before
    a new registry is returned. Callables are not executed by this operation.
    """
    if isinstance(tools, ToolRegistry):
        return tools
    if tools is None:
        return ToolRegistry()
    if isinstance(tools, Mapping):
        if any(not isinstance(alias, str) for alias in tools):
            raise ValidationError("Tool aliases must be strings")
        entries = tools.items()
    elif isinstance(tools, Iterable) and not isinstance(tools, (str, bytes, bytearray)):
        entries = ((None, value) for value in tools)
    else:
        raise ValidationError("tools must be a ToolRegistry, iterable of tools, or mapping")
    specs = []
    names = set()
    for alias, value in entries:
        if alias is not None and not isinstance(alias, str):
            raise ValidationError("Tool aliases must be strings")
        if isinstance(value, ToolSpec):
            spec = value if alias is None else replace(value, name=alias)
        else:
            if not callable(value):
                raise ValidationError("Every tool must be a callable or ToolSpec")
            spec = tool(value, name=alias)
        if spec.name in names:
            raise ValidationError(f"Duplicate tool: {spec.name}")
        names.add(spec.name)
        specs.append(spec)
    return ToolRegistry(specs)
