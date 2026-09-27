"""Append-only event journal with verifiable settlement and optional SQLite WAL."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from flora.support.errors import (
    InterruptedEffect,
    StaleAnchor,
    TraceIntegrityError,
    ValidationError,
)
from flora.support.resources import (
    DEFAULT_CHECKPOINT_BYTES,
    DEFAULT_JOURNAL_BYTES,
    TRACE_PART_DEPTH,
    TRACE_PART_NODES,
    ResourceLimitExceeded,
    encoded_size,
    validate_limit,
)
from flora.support.values import MAX_ENCODED_BYTES, canonical_json, clone, digest

GENESIS = "0" * 64


def _part_size(value, limit, resource):
    return encoded_size(
        value,
        limit=limit,
        resource=resource,
        max_nodes=TRACE_PART_NODES,
        max_depth=TRACE_PART_DEPTH,
    )


def _reduce(entries: list[dict]) -> tuple[list[dict], str]:
    if type(entries) is not list:
        raise TraceIntegrityError("Journal entries must be a list")
    records: list[dict] = []
    previous = GENESIS
    for seq, entry in enumerate(entries):
        if type(entry) is not dict or set(entry) != {
            "seq",
            "op",
            "payload",
            "previous_hash",
            "hash",
        }:
            raise TraceIntegrityError("Invalid journal entry fields")
        body = {k: v for k, v in entry.items() if k != "hash"}
        if (
            type(entry["seq"]) is not int
            or entry["seq"] != seq
            or entry["previous_hash"] != previous
            or digest(body) != entry["hash"]
        ):
            raise TraceIntegrityError(f"Journal hash chain invalid at sequence {seq}")
        p = entry["payload"]
        if type(p) is not dict:
            raise TraceIntegrityError("Journal payload must be an object")
        if type(entry["op"]) is not str:
            raise TraceIntegrityError("Journal operation must be a string")
        if entry["op"] == "begin":
            if set(p) != {"event_id", "tool", "args"}:
                raise TraceIntegrityError("Invalid begin fields")
            if (
                type(p.get("event_id")) is not int
                or p["event_id"] != len(records)
                or any(x["status"] in {"pending", "interrupted_unknown"} for x in records)
            ):
                raise TraceIntegrityError("Invalid or overlapping pending event")
            if (
                not isinstance(p.get("tool"), str)
                or not p["tool"]
                or not isinstance(p.get("args"), dict)
            ):
                raise TraceIntegrityError("Invalid request record")
            records.append(
                {
                    "event_id": p["event_id"],
                    "tool": p["tool"],
                    "args": p["args"],
                    "status": "pending",
                    "anchor_epoch": len(records),
                    "anchor_digest": previous,
                    "previous_hash": previous,
                    "begin_hash": entry["hash"],
                }
            )
        elif entry["op"] in {"settle", "resolve"}:
            expected_fields = {"event_id", "outcome"} | (
                {"reason"} if entry["op"] == "resolve" else set()
            )
            if set(p) != expected_fields:
                raise TraceIntegrityError("Invalid settlement fields")
            idx = p.get("event_id")
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(records):
                raise TraceIntegrityError("Settlement event does not exist")
            valid_prior = (
                {"pending"} if entry["op"] == "settle" else {"pending", "interrupted_unknown"}
            )
            if records[idx]["status"] not in valid_prior:
                raise TraceIntegrityError("Event already settled")
            outcome = p.get("outcome", {})
            try:
                _validate_outcome(outcome)
            except ValidationError as exc:
                raise TraceIntegrityError("Invalid persisted effect outcome") from exc
            records[idx].update(clone(outcome))
            records[idx]["settlement_hash"] = entry["hash"]
            if entry["op"] == "resolve":
                if (
                    outcome["status"] == "interrupted_unknown"
                    or not isinstance(p.get("reason"), str)
                    or not p["reason"].strip()
                ):
                    raise TraceIntegrityError("Manual resolution requires provenance")
                records[idx]["resolution_reason"] = p["reason"]
        else:
            raise TraceIntegrityError("Unknown journal operation")
        previous = entry["hash"]
    return records, previous


def _validate_outcome(outcome: dict) -> None:
    if (
        not isinstance(outcome, dict)
        or not isinstance(outcome.get("status"), str)
        or outcome["status"] not in {"returned", "raised", "interrupted_unknown"}
    ):
        raise ValidationError("Invalid effect outcome")
    if outcome["status"] == "returned" and set(outcome) != {"status", "value"}:
        raise ValidationError("Returned outcome needs only status and value")
    if outcome["status"] != "returned":
        if set(outcome) != {"status", "error"} or not isinstance(outcome["error"], dict):
            raise ValidationError("Error outcome requires an error object")
        if set(outcome["error"]) != {"type", "message"} or not all(
            isinstance(v, str) for v in outcome["error"].values()
        ):
            raise ValidationError("Invalid error object")
    canonical_json(outcome)


class MemoryTrace:
    """Single real event stream. Copies prevent candidate mutation of receipts."""

    def __init__(
        self,
        *,
        max_journal_bytes: int = DEFAULT_JOURNAL_BYTES,
        max_checkpoint_bytes: int = DEFAULT_CHECKPOINT_BYTES,
    ) -> None:
        self.max_journal_bytes = validate_limit(max_journal_bytes, "max_journal_bytes")
        self.max_checkpoint_bytes = validate_limit(max_checkpoint_bytes, "max_checkpoint_bytes")
        if self.max_journal_bytes + self.max_checkpoint_bytes + 4096 > MAX_ENCODED_BYTES:
            raise ValidationError(
                "Journal and checkpoint limits must leave 4096 bytes within the 16 MiB JSON limit"
            )
        self._entries: list[dict] = []
        self._checkpoint: dict | None = None
        self._lock = threading.RLock()

    def journal(self) -> list[dict]:
        with self._lock:
            return clone(self._entries)

    @property
    def records(self) -> list[dict]:
        with self._lock:
            return _reduce(self.journal())[0]

    @property
    def epoch(self) -> int:
        return len(self.records)

    @property
    def digest(self) -> str:
        return _reduce(self.journal())[1]

    def _entry(self, op: str, payload: dict) -> dict:
        body = {
            "seq": len(self._entries),
            "op": op,
            "payload": clone(payload),
            "previous_hash": self.digest,
        }
        return {**body, "hash": digest(body)}

    def _check_entry_capacity(self, entry: dict) -> None:
        _part_size([*self._entries, entry], self.max_journal_bytes, "real journal")

    def _append(self, op: str, payload: dict) -> None:
        entry = self._entry(op, payload)
        self._check_entry_capacity(entry)
        self._entries.append(entry)

    def reserve_effect(self, tool: str, args: dict, *, max_output_bytes: int) -> None:
        """Check worst-case admission capacity before the host action is called.

        This is a capacity check, not a durable reservation or permission token.
        ``begin`` must still use the same expected history CAS. No journal facts
        are evicted; conservative admission may stop with unused capacity.
        """
        validate_limit(max_output_bytes, "max_output_bytes", minimum=1)
        with self._lock:
            self.journal()  # refresh a durable store before sizing the actual prefix
            begin = self._entry("begin", {"event_id": self.epoch, "tool": tool, "args": args})
            body = {
                "seq": len(self._entries) + 1,
                "op": "settle",
                "payload": {
                    "event_id": self.epoch,
                    "outcome": {"status": "returned", "value": None},
                },
                "previous_hash": begin["hash"],
            }
            settle = {**body, "hash": digest(body)}
            # Check room for fallback metadata even when a result exceeds the
            # independent JSON node limit. 16 KiB also bounds escaped errors.
            unknown = {
                **settle,
                "payload": {
                    "event_id": self.epoch,
                    "outcome": {
                        "status": "interrupted_unknown",
                        "error": {
                            "type": "ObservationUnavailable",
                            "message": "Tool completed but its result could not be faithfully stored within configured limits",
                        },
                    },
                },
            }
            try:
                _part_size([*self._entries, begin, unknown], self.max_journal_bytes, "real journal")
            except ValidationError as exc:
                raise ResourceLimitExceeded(
                    "real journal JSON structure", self.max_journal_bytes
                ) from exc
            required = (
                encoded_size([*self._entries, begin, settle], resource="real journal")
                - 4
                + max(max_output_bytes, 16_384)
            )
            if required > self.max_journal_bytes:
                raise ResourceLimitExceeded(
                    "real journal admission", self.max_journal_bytes, required
                )

    def check_settlement_capacity(self, event_id: int, outcome: dict) -> None:
        """Read-only storage check; a completed but unstoreable result is UNKNOWN."""
        with self._lock:
            self.journal()
            self._check_entry_capacity(
                self._entry("settle", {"event_id": event_id, "outcome": outcome})
            )

    def begin(self, tool: str, args: dict, *, expected_epoch: int, expected_digest: str) -> int:
        with self._lock:
            if self.epoch != expected_epoch or self.digest != expected_digest:
                raise StaleAnchor("Request was produced against another real history")
            if any(r["status"] in {"pending", "interrupted_unknown"} for r in self.records):
                raise InterruptedEffect("Unresolved external effect blocks new effects")
            if not isinstance(tool, str) or not tool or not isinstance(args, dict):
                raise ValidationError("Invalid effect request")
            event_id = self.epoch
            self._append("begin", {"event_id": event_id, "tool": tool, "args": clone(args)})
            return event_id

    def settle(self, event_id: int, outcome: dict) -> dict:
        with self._lock:
            _validate_outcome(outcome)
            records = self.records
            if (
                not isinstance(event_id, int)
                or isinstance(event_id, bool)
                or not 0 <= event_id < len(records)
                or records[event_id]["status"] != "pending"
            ):
                raise ValidationError("No pending event to settle")
            self._append("settle", {"event_id": event_id, "outcome": outcome})
            return self.records[event_id]

    def resolve(self, event_id: int, outcome: dict, *, reason: str) -> dict:
        """Operator supplies known outcome; never executes a tool or assumes rollback."""
        with self._lock:
            _validate_outcome(outcome)
            if (
                outcome["status"] == "interrupted_unknown"
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise ValidationError("Resolution needs a known outcome and a reason")
            records = self.records
            if (
                not isinstance(event_id, int)
                or isinstance(event_id, bool)
                or not 0 <= event_id < len(records)
                or records[event_id]["status"] not in {"pending", "interrupted_unknown"}
            ):
                raise ValidationError("Event is not unresolved")
            self._append("resolve", {"event_id": event_id, "outcome": outcome, "reason": reason})
            return self.records[event_id]

    def save_checkpoint(self, data: dict) -> None:
        with self._lock:
            _part_size(data, self.max_checkpoint_bytes, "runtime checkpoint")
            self._checkpoint = clone(data)

    def load_checkpoint(self) -> dict | None:
        with self._lock:
            return clone(self._checkpoint) if self._checkpoint is not None else None

    def export(self) -> dict:
        return {
            "format": "openharness-trace-v1",
            "journal": self.journal(),
            "checkpoint": self.load_checkpoint(),
            "limits": {
                "max_journal_bytes": self.max_journal_bytes,
                "max_checkpoint_bytes": self.max_checkpoint_bytes,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> MemoryTrace:
        if not isinstance(data, dict) or data.get("format") != "openharness-trace-v1":
            raise TraceIntegrityError("Unrecognized trace format")
        if "journal" not in data or type(data["journal"]) is not list:
            raise TraceIntegrityError("Trace requires a journal array")
        limits = data.get("limits", {})
        if not isinstance(limits, dict) or set(limits) - {
            "max_journal_bytes",
            "max_checkpoint_bytes",
        }:
            raise TraceIntegrityError("Invalid trace storage limits")
        obj = cls(**limits)
        _part_size(data["journal"], obj.max_journal_bytes, "real journal")
        _reduce(data["journal"])
        if data.get("checkpoint") is not None:
            _part_size(data["checkpoint"], obj.max_checkpoint_bytes, "runtime checkpoint")
        obj._entries = clone(data["journal"])
        obj._checkpoint = clone(data.get("checkpoint"))
        return obj

    def close(self) -> None:
        pass


class SQLiteTrace(MemoryTrace):
    """Durable local journal. CAS admission serializes effects across connections.

    This prevents automatic duplicate invocation after a crash; it cannot make
    an arbitrary external API exactly-once. Pending outcomes require resolution.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_journal_bytes: int = DEFAULT_JOURNAL_BYTES,
        max_checkpoint_bytes: int = DEFAULT_CHECKPOINT_BYTES,
    ) -> None:
        super().__init__(
            max_journal_bytes=max_journal_bytes, max_checkpoint_bytes=max_checkpoint_bytes
        )
        self.path = str(path)
        self._db = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False, timeout=30
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS journal (seq INTEGER PRIMARY KEY, entry TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)"
        )
        try:
            self._refresh()
            _reduce(self._entries)
        except BaseException:
            self._db.close()
            raise

    def _refresh(self) -> None:
        byte_count, rows = self._db.execute(
            "SELECT COALESCE(SUM(length(CAST(entry AS BLOB))),0), COUNT(*) FROM journal"
        ).fetchone()
        required = byte_count + max(0, rows - 1) + 2
        if required > self.max_journal_bytes:
            raise ResourceLimitExceeded("real journal", self.max_journal_bytes, required)
        entries = []
        for expected, (row_seq, encoded) in enumerate(
            self._db.execute("SELECT seq, entry FROM journal ORDER BY seq")
        ):
            if type(row_seq) is not int or row_seq != expected:
                raise TraceIntegrityError("SQLite journal sequence is not contiguous from zero")
            entries.append(json.loads(encoded))
        self._entries = entries
        _part_size(self._entries, self.max_journal_bytes, "real journal")

    def journal(self) -> list[dict]:
        with self._lock:
            self._refresh()
            return clone(self._entries)

    def _append(self, op: str, payload: dict) -> None:
        self._refresh()
        body = {
            "seq": len(self._entries),
            "op": op,
            "payload": clone(payload),
            "previous_hash": _reduce(self._entries)[1],
        }
        entry = {**body, "hash": digest(body)}
        self._check_entry_capacity(entry)
        self._db.execute(
            "INSERT INTO journal(seq,entry) VALUES (?,?)", (entry["seq"], canonical_json(entry))
        )
        self._entries.append(entry)

    def _transaction(self, method, *args, **kwargs):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._refresh()
                result = method(*args, **kwargs)
                self._db.execute("COMMIT")
                return result
            except BaseException:
                self._db.execute("ROLLBACK")
                self._refresh()
                raise

    def begin(self, tool, args, *, expected_epoch, expected_digest):
        return self._transaction(
            super().begin,
            tool,
            args,
            expected_epoch=expected_epoch,
            expected_digest=expected_digest,
        )

    def settle(self, event_id, outcome):
        return self._transaction(super().settle, event_id, outcome)

    def resolve(self, event_id, outcome, *, reason):
        return self._transaction(super().resolve, event_id, outcome, reason=reason)

    def save_checkpoint(self, data: dict) -> None:
        with self._lock:
            _part_size(data, self.max_checkpoint_bytes, "runtime checkpoint")
            self._db.execute(
                "INSERT INTO checkpoint(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (canonical_json(data),),
            )

    def load_checkpoint(self) -> dict | None:
        with self._lock:
            size = self._db.execute(
                "SELECT length(CAST(data AS BLOB)) FROM checkpoint WHERE id=1"
            ).fetchone()
            if size and size[0] > self.max_checkpoint_bytes:
                raise ResourceLimitExceeded(
                    "runtime checkpoint", self.max_checkpoint_bytes, size[0]
                )
            row = self._db.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
            value = json.loads(row[0]) if row else None
            if value is not None:
                _part_size(value, self.max_checkpoint_bytes, "runtime checkpoint")
            return value

    def close(self) -> None:
        with self._lock:
            self._db.close()


def outcome_from_record(record: dict) -> dict:
    status = record.get("status")
    if status == "returned":
        return {"status": status, "value": clone(record["value"])}
    if status in {"raised", "interrupted_unknown"}:
        return {"status": status, "error": clone(record["error"])}
    raise InterruptedEffect("No known outcome for pending event")
