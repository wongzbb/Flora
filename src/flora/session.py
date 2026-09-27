# SPDX-License-Identifier: Apache-2.0
"""Local session metadata and exclusive mutation locks.

Each turn has its own real-effect journal. Session metadata references those
journals and carries the cumulative budget; it never copies an old receipt into
a new turn's real history. Locks coordinate cooperating local processes, not a
distributed worker fleet or arbitrary external writers.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .errors import FloraError, ValidationError
from .trace import MemoryTrace, SQLiteTrace
from .values import canonical_json, clone

SESSION_FORMAT = "openharness-session-v1"
MAX_HISTORY_TURNS = 32
MAX_HISTORY_BYTES = 64 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024


class SessionBusyError(FloraError):
    """Another call or local process currently owns this session."""


class SessionStateError(FloraError):
    """Session identity or unfinished work requires an explicit user decision."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Session metadata contains duplicate JSON keys")
        result[key] = value
    return result


def history_append(history: dict, record: dict) -> dict:
    """Keep exact newest turn records; dropping an entry is explicitly counted."""
    result = clone(history)
    result["turns"].append(clone(record))
    while result["turns"] and (
        len(result["turns"]) > MAX_HISTORY_TURNS
        or len(canonical_json(result).encode("utf-8")) > MAX_HISTORY_BYTES
    ):
        result["turns"].pop(0)
        result["omitted_turns"] += 1
    return result


def empty_history() -> dict:
    return {
        "turns": [],
        "omitted_turns": 0,
        "limit_turns": MAX_HISTORY_TURNS,
        "limit_bytes": MAX_HISTORY_BYTES,
        "representation": "exact_retained_records; omitted_records_are_not_summarized",
    }


class SessionStore:
    """Internal atomic state store; use :class:`Agent` for normal interaction."""

    def __init__(self, directory=None):
        self.directory = None
        self._lock = threading.Lock()
        self._memory = None
        self._traces: dict[str, MemoryTrace] = {}
        if directory is not None:
            target = Path(directory).expanduser()
            if target.is_symlink():
                raise SessionStateError("Session directory must not be a symbolic link")
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not target.is_dir():
                raise SessionStateError("session_dir must identify a directory")
            self.directory = target.resolve()

    @contextmanager
    def locked(self):
        if not self._lock.acquire(blocking=False):
            raise SessionBusyError("Session is busy; wait for the running task to finish")
        fd = None
        try:
            if self.directory is not None:
                flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.directory / ".session.lock", flags, 0o600)
                try:
                    if os.name == "nt":
                        import msvcrt

                        if os.fstat(fd).st_size == 0:
                            os.write(fd, b"0")
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError) as exc:
                    raise SessionBusyError(
                        "Session is busy in another process; wait before retrying"
                    ) from exc
            yield
        finally:
            if fd is not None:
                os.close(fd)
            self._lock.release()

    def load(self):
        if self.directory is None:
            return clone(self._memory) if self._memory is not None else None
        path = self.directory / "session.json"
        if not path.exists():
            if any(self.directory.glob("turn-*.sqlite")):
                raise SessionStateError(
                    "Session metadata is missing but turn journals remain. Restore a verified "
                    "session.json backup; do not delete journals or silently reset the budget."
                )
            return None
        if path.is_symlink() or path.stat().st_size > MAX_METADATA_BYTES:
            raise SessionStateError("Session metadata is a symlink or exceeds the storage bound")
        try:
            state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            canonical_json(state)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SessionStateError(
                "Session metadata is invalid; restore a verified backup"
            ) from exc
        self._validate(state)
        return state

    @staticmethod
    def _validate(state):
        if not isinstance(state, dict) or state.get("format") != SESSION_FORMAT:
            raise SessionStateError("Unsupported session metadata format")
        if (
            not isinstance(state.get("fingerprint"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", state["fingerprint"])
            or not isinstance(state.get("budget"), dict)
            or not isinstance(state.get("reuse"), dict)
            or type(state.get("reuse_variants_omitted")) is not int
            or state["reuse_variants_omitted"] < 0
        ):
            raise SessionStateError("Invalid session identity, budget or reuse metadata")
        for name in ("revision", "next_turn", "completed_turns"):
            if type(state.get(name)) is not int or state[name] < (1 if name == "next_turn" else 0):
                raise SessionStateError(f"Invalid session counter: {name}")
        history = state.get("history")
        if (
            not isinstance(history, dict)
            or not isinstance(history.get("turns"), list)
            or type(history.get("omitted_turns")) is not int
            or history["omitted_turns"] < 0
            or len(history["turns"]) > MAX_HISTORY_TURNS
            or len(canonical_json(history).encode("utf-8")) > MAX_HISTORY_BYTES
        ):
            raise SessionStateError("Invalid bounded session history")
        active = state.get("active")
        if active is not None:
            if (
                not isinstance(active, dict)
                or type(active.get("turn")) is not int
                or active["turn"] < 1
                or not isinstance(active.get("task"), str)
                or not isinstance(active.get("memory"), dict)
                or not isinstance(active.get("baseline_budget"), dict)
            ):
                raise SessionStateError("Invalid active session turn")
            if (
                type(active["baseline_budget"].get("tool_calls")) is not int
                or active["baseline_budget"]["tool_calls"] < 0
                or active["turn"] >= state["next_turn"]
            ):
                raise SessionStateError("Invalid active session budget baseline or turn sequence")
            SessionStore.trace_name(active.get("trace"))
        if state.get("last_trace") is not None:
            SessionStore.trace_name(state["last_trace"])

    @staticmethod
    def trace_name(name):
        if not isinstance(name, str) or not re.fullmatch(r"turn-[0-9]{8,16}\.sqlite", name):
            raise SessionStateError("Invalid session journal reference")
        return name

    def save(self, state):
        self._validate(state)
        encoded = canonical_json(state).encode("utf-8")
        if len(encoded) > MAX_METADATA_BYTES:
            raise SessionStateError("Session metadata exceeds its 8 MiB storage limit")
        if self.directory is None:
            self._memory = clone(state)
            return
        fd, name = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.directory / "session.json")
            if os.name != "nt":
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def trace(self, name: str, *, create=False):
        name = self.trace_name(name)
        if self.directory is None:
            if create:
                if name in self._traces:
                    raise SessionStateError("A journal already exists for this turn")
                self._traces[name] = MemoryTrace()
            if name not in self._traces:
                raise SessionStateError("Active session journal is missing")
            return self._traces[name]
        path = self.directory / name
        if path.is_symlink():
            raise SessionStateError("Session journal must not be a symbolic link")
        if create:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                raise SessionStateError("A journal already exists for this turn") from exc
            os.close(fd)
        elif not path.is_file():
            raise SessionStateError("Active journal is missing; refusing to reset real history")
        return SQLiteTrace(path)

    def release_trace(self, trace):
        if self.directory is not None:
            trace.close()

    def close(self):
        for trace in self._traces.values():
            trace.close()
        self._traces.clear()
