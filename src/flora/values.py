# SPDX-License-Identifier: Apache-2.0
"""Strict finite JSON values shared by the interpreter and trace store.

Only exact Python JSON container/scalar types are accepted; objects, subclasses,
non-string keys, non-finite floats, cycles, and excessively deep values fail.
There is deliberately no repr fallback or implicit host-object access.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import ValidationError

MAX_INTEGER_BITS = 4096
MAX_DEPTH = 64
MAX_NODES = 200_000
MAX_ENCODED_BYTES = 16 * 1024 * 1024


def validate_json(value: Any, *, max_depth: int = MAX_DEPTH, max_nodes: int = MAX_NODES) -> None:
    pending = [(value, 0, frozenset())]
    visited = 0
    while pending:
        current, depth, ancestors = pending.pop()
        visited += 1
        if visited > max_nodes:
            raise ValidationError("JSON value exceeds the node limit")
        if depth > max_depth:
            raise ValidationError("JSON value exceeds the nesting limit")
        kind = type(current)
        if kind in (type(None), bool, str):
            if kind is str:
                try:
                    current.encode("utf-8")
                except UnicodeError as error:
                    raise ValidationError("JSON strings must contain valid Unicode") from error
            continue
        if kind is int:
            if current.bit_length() > MAX_INTEGER_BITS:
                raise ValidationError("JSON integer exceeds the bit limit")
            continue
        if kind is float:
            if not math.isfinite(current):
                raise ValidationError("non-finite JSON number")
            continue
        if kind not in (dict, list):
            raise ValidationError(f"unsupported JSON value type: {kind.__name__}")
        # Bound the work queue before materializing child entries. This also
        # bounds expansion of small shared DAGs passed by host code.
        children = len(current) * (2 if kind is dict else 1)
        if visited + len(pending) + children > max_nodes:
            raise ValidationError("JSON value exceeds the node limit")
        identity = id(current)
        if identity in ancestors:
            raise ValidationError("cyclic JSON value")
        lineage = ancestors | {identity}
        if kind is dict:
            if any(type(key) is not str for key in current):
                raise ValidationError("JSON object keys must be strings")
            # Validate keys too, including invalid surrogate characters.
            pending.extend((key, depth + 1, lineage) for key in current)
            pending.extend((item, depth + 1, lineage) for item in current.values())
        else:
            pending.extend((item, depth + 1, lineage) for item in current)


def _encode(value: Any, maximum: int) -> str:
    validate_json(value)
    try:
        encoder = json.JSONEncoder(
            ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        pieces = []
        total = 0
        for piece in encoder.iterencode(value):
            total += len(piece.encode("utf-8"))
            if total > maximum:
                raise ValidationError("JSON value exceeds the serialized byte limit")
            pieces.append(piece)
        return "".join(pieces)
    except (ValueError, TypeError, RecursionError) as error:
        if isinstance(error, ValidationError):
            raise
        raise ValidationError("cannot encode strict JSON") from error


def canonical_json(value: Any) -> str:
    """Canonical strict JSON, with a 16 MiB per-value serialization limit."""
    return _encode(value, MAX_ENCODED_BYTES)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def bounded_clone(value: Any, max_bytes: int) -> Any:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValidationError("max_value_bytes must be a positive integer")
    encoded = _encode(value, min(max_bytes, MAX_ENCODED_BYTES))
    return json.loads(encoded)
