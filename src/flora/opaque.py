# SPDX-License-Identifier: Apache-2.0
"""Process-local capabilities for tool results which are not ordinary JSON.

An opaque carrier is a JSON *reference*, never a snapshot or a claim about an
object's current state. Objects are held strongly in this store and are returned
by identity only to explicitly opted-in host-tool parameters. Carriers cannot be
restored after process/store loss: there is deliberately no pickle, repr, object
attribute traversal, or persistent object reconstruction.
"""

from __future__ import annotations

import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from .errors import ValidationError
from .values import MAX_DEPTH, MAX_ENCODED_BYTES, MAX_NODES, canonical_json, clone

OPAQUE_KEY = "__openharness_opaque__"


def is_opaque(value: Any) -> bool:
    """Recognize the carrier's exact JSON shape, without authenticating its token."""
    if type(value) is not dict or set(value) != {OPAQUE_KEY}:
        return False
    data = value[OPAQUE_KEY]
    return (
        type(data) is dict
        and set(data) == {"token", "type"}
        and type(data["token"]) is str
        and bool(data["token"])
        and type(data["type"]) is str
        and bool(data["type"])
    )


def contains_opaque(value: Any) -> bool:
    """Find any reserved key, including malformed carriers, in ordinary JSON.

    This is a syntactic check, not authorization. A carrier with an invented token
    still contains the marker. Non-JSON host objects are not traversed.
    """
    pending = [value]
    seen = set()
    while pending:
        item = pending.pop()
        if type(item) not in (dict, list):
            continue
        if id(item) in seen:
            continue
        seen.add(id(item))
        if type(item) is dict:
            if OPAQUE_KEY in item:
                return True
            pending.extend(item.values())
        else:
            pending.extend(item)
    return False


@dataclass(frozen=True)
class _Entry:
    value: Any
    type_name: str
    expires_at: float | None


def _type_name(value: Any) -> str:
    # Calling type.__getattribute__ would still invoke a metaclass descriptor.
    # Invoke the builtin name descriptor directly; never execute user metadata.
    name = type.__dict__["__name__"].__get__(type(value))
    return re.sub(r"[^A-Za-z0-9_]", "_", name)[:96] or "object"


class OpaqueStore:
    """Bounded, process-local store of unforgeable live-object references.

    ``max_handles`` bounds retained objects, not their host memory footprints.
    Adapters remain responsible for the memory/resources of objects they return.
    Optional TTL is measured from first registration, not refreshed by access.
    Revocation, expiry, clear(), or loss of this store makes a carrier unusable.
    Equal carriers denote identity only; the underlying object may have mutated.
    """

    def __init__(self, *, max_handles: int = 1024, ttl_seconds: float | None = None) -> None:
        if type(max_handles) is not int or not 1 <= max_handles <= 100000:
            raise ValidationError("opaque max_handles must be between 1 and 100000")
        if ttl_seconds is not None:
            try:
                valid_ttl = (
                    type(ttl_seconds) in (int, float)
                    and math.isfinite(ttl_seconds)
                    and ttl_seconds > 0
                )
            except OverflowError:
                valid_ttl = False
            if not valid_ttl:
                raise ValidationError("opaque TTL must be a positive finite number")
        self.max_handles = max_handles
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, _Entry] = {}
        self._identities: dict[int, str] = {}
        self._lock = threading.RLock()

    def _remove(self, token: str) -> None:
        entry = self._entries.pop(token)
        self._identities.pop(id(entry.value), None)

    def _expire(self, now: float) -> None:
        expired = [
            token
            for token, entry in self._entries.items()
            if entry.expires_at is not None and now >= entry.expires_at
        ]
        for token in expired:
            self._remove(token)

    def register(self, value: Any) -> dict[str, Any]:
        """Register by identity without copying or inspecting an object's contents."""
        with self._lock:
            now = time.monotonic()
            self._expire(now)
            token = self._identities.get(id(value))
            if token is None:
                if len(self._entries) >= self.max_handles:
                    raise ValidationError("opaque handle capacity exhausted")
                token = secrets.token_urlsafe(32)
                while token in self._entries:
                    token = secrets.token_urlsafe(32)
                entry = _Entry(
                    value,
                    _type_name(value),
                    None if self.ttl_seconds is None else now + self.ttl_seconds,
                )
                self._entries[token] = entry
                self._identities[id(value)] = token
            return {OPAQUE_KEY: {"token": token, "type": self._entries[token].type_name}}

    def resolve(self, carrier: dict[str, Any]) -> Any:
        """Authenticate a carrier and return its original live object by identity."""
        if not is_opaque(carrier):
            raise ValidationError("invalid opaque carrier shape")
        with self._lock:
            self._expire(time.monotonic())
            data = carrier[OPAQUE_KEY]
            entry = self._entries.get(data["token"])
            if entry is None or entry.type_name != data["type"]:
                raise ValidationError("unknown, expired, or foreign opaque handle")
            return entry.value

    def revoke(self, carrier: dict[str, Any]) -> None:
        """Explicitly release one authenticated reference; future uses fail closed."""
        with self._lock:
            self.resolve(carrier)
            self._remove(carrier[OPAQUE_KEY]["token"])

    def clear(self) -> None:
        """Release all retained objects. Existing serialized carriers become stale."""
        with self._lock:
            self._entries.clear()
            self._identities.clear()

    def stats(self) -> dict[str, Any]:
        """Return capacity information without exposing object contents or tokens."""
        with self._lock:
            self._expire(time.monotonic())
            return {
                "live_handles": len(self._entries),
                "max_handles": self.max_handles,
                "ttl_seconds": self.ttl_seconds,
                "persistence": "process_local",
            }

    def encode_result(self, value: Any) -> Any:
        """Copy ordinary JSON and replace only non-JSON components with carriers.

        Exact dict/list containers are traversed, never subclasses or attributes.
        A dict with the reserved key is made opaque as a whole, so host data cannot
        forge a carrier. Non-string-key dictionaries and cyclic/deep backedges are
        similarly opaque. Nonfinite floats are opaque rather than fabricated JSON.
        Node/serialized-size bounds still apply to the resulting ordinary JSON.
        Newly allocated handles are released if encoding fails.
        """
        with self._lock:
            self._expire(time.monotonic())
            previous = set(self._entries)
            visited = 0
            encoded_bytes = 0

            def charge(amount: int) -> None:
                nonlocal encoded_bytes
                encoded_bytes += amount
                if encoded_bytes > MAX_ENCODED_BYTES:
                    raise ValidationError("tool result exceeds the JSON serialized byte limit")

            def carrier(item: Any) -> dict[str, Any]:
                encoded = self.register(item)
                charge(len(canonical_json(encoded).encode("utf-8")))
                return encoded

            def encode(item: Any, active: set[int], depth: int) -> Any:
                nonlocal visited
                visited += 1
                if visited > MAX_NODES:
                    raise ValidationError("tool result exceeds the JSON node limit")
                kind = type(item)
                if kind in (type(None), bool, int, str):
                    charge(len(canonical_json(item).encode("utf-8")))
                    return clone(item)
                if kind is float:
                    if not math.isfinite(item):
                        return carrier(item)
                    charge(len(canonical_json(item).encode("utf-8")))
                    return item
                if kind not in (dict, list):
                    return carrier(item)
                # Reserve enough nesting depth for the two-level carrier itself.
                if depth >= MAX_DEPTH - 3 or id(item) in active:
                    return carrier(item)
                if kind is dict and (
                    OPAQUE_KEY in item or any(type(key) is not str for key in item)
                ):
                    return carrier(item)
                if len(item) + visited > MAX_NODES:
                    raise ValidationError("tool result exceeds the JSON node limit")
                active.add(id(item))
                charge(2 + max(0, len(item) - 1))  # Brackets/braces and commas.
                try:
                    if kind is list:
                        return [encode(child, active, depth + 1) for child in item]
                    result = {}
                    for key, child in item.items():
                        visited += 1
                        charge(len(canonical_json(key).encode("utf-8")) + 1)
                        result[key] = encode(child, active, depth + 1)
                    return result
                finally:
                    active.remove(id(item))

            try:
                result = encode(value, set(), 0)
                canonical_json(result)
                return result
            except Exception:
                for token in set(self._entries) - previous:
                    self._remove(token)
                raise
