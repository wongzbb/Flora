# SPDX-License-Identifier: Apache-2.0
"""Explicit serialization budgets and whole-record optional-evidence retention.

Byte counts describe canonical UTF-8 JSON, not Python heap consumption. JSON
depth/node limits in values.py apply independently. Real journals must never be
passed to ``retain_newest``: only replaceable reports and local check witnesses
may be evicted.
"""

from __future__ import annotations

import json
from typing import Any

from flora.support.errors import BudgetExceeded, ValidationError
from flora.support.values import MAX_DEPTH, MAX_ENCODED_BYTES, MAX_NODES, validate_json

MIB = 1024 * 1024
DEFAULT_JOURNAL_BYTES = 7 * MIB
DEFAULT_CHECKPOINT_BYTES = 7 * MIB
DEFAULT_CONTEXT_BYTES = MIB
DEFAULT_CONTRACT_BYTES = MIB
DEFAULT_REPORT_BYTES = 256 * 1024
TRACE_PART_NODES = 90_000  # journal + checkpoint + export envelope fit MAX_NODES
TRACE_PART_DEPTH = 62  # leave room for the export envelope


class ResourceLimitExceeded(BudgetExceeded):
    """A host storage limit was reached; no permission to retry an effect."""

    def __init__(self, resource: str, limit: int, required: int | None = None):
        self.resource, self.limit, self.required = resource, limit, required
        amount = "" if required is None else f" (requires at least {required} bytes)"
        super().__init__(f"{resource} exceeds {limit} serialized bytes{amount}")


def validate_limit(value: int, name: str, *, minimum: int = 2) -> int:
    if type(value) is not int or not minimum <= value <= MAX_ENCODED_BYTES:
        raise ValidationError(f"{name} must be an integer in [{minimum}, {MAX_ENCODED_BYTES}]")
    return value


def encoded_size(
    value: Any,
    *,
    limit: int = MAX_ENCODED_BYTES,
    resource: str = "JSON value",
    max_nodes: int = MAX_NODES,
    max_depth: int = MAX_DEPTH,
) -> int:
    """Count without assembling an additional aggregate serialization string."""
    validate_limit(limit, "byte limit")
    validate_json(value, max_nodes=max_nodes, max_depth=max_depth)
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    total = 0
    for piece in encoder.iterencode(value):
        total += len(piece.encode("utf-8"))
        if total > limit:
            raise ResourceLimitExceeded(resource, limit, total)
    return total


def retain_newest(
    records: list[dict],
    *,
    max_bytes: int,
    max_records: int | None = None,
    resource: str = "optional records",
) -> tuple[list[dict], dict[str, int]]:
    """Retain newest whole records, never cut a record or relabel a verdict.

    An individually oversized record is omitted without destroying older valid
    witnesses. Once the remaining capacity is exhausted, older records are also
    omitted. Retention is explicit; it makes no claim of a gap-free history.
    Returned records share references; callers own the records and clone at their
    public API boundaries.
    """
    validate_limit(max_bytes, "max_bytes")
    if max_records is not None and (type(max_records) is not int or max_records < 0):
        raise ValidationError("max_records must be a nonnegative integer")
    kept: list[dict] = []
    total = 2  # brackets, including the empty-list case
    for record in reversed(records):
        if max_records is not None and len(kept) >= max_records:
            break
        try:
            size = encoded_size(record, limit=max_bytes, resource=resource)
        except (ResourceLimitExceeded, ValidationError):
            continue
        added = size + bool(kept)
        if total + added > max_bytes:
            break
        kept.append(record)
        total += added
    kept.reverse()
    # Independent aggregate node/depth limits still apply. Drop complete oldest
    # witnesses when repeating large structured inputs reaches that bound first.
    while kept:
        try:
            encoded_size(kept, limit=max_bytes, resource=resource)
            break
        except (ResourceLimitExceeded, ValidationError):
            del kept[0]
    return kept, {
        "dropped_records": len(records) - len(kept),
        "retained_records": len(kept),
        "retained_bytes": encoded_size(kept, limit=max_bytes, resource=resource),
    }
