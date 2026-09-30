# SPDX-License-Identifier: Apache-2.0
"""Durable, bounded observations and artifact metadata, separate from model context."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from flora.support.errors import ValidationError


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".flora-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Lease:
    def __init__(self, directory):
        self.file = open(Path(directory) / "application.lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.file.write(b"\0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValidationError("This general-agent session is already open") from None

    def close(self):
        self.file.close()


class ObservationStore:
    def __init__(self, directory, *, max_bytes=268435456, max_item_bytes=16777216):
        self.max_bytes, self.max_item_bytes = max_bytes, max_item_bytes
        self.lock = threading.RLock()
        self.db = sqlite3.connect(Path(directory) / "observations.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sources(
              id INTEGER PRIMARY KEY, origin TEXT NOT NULL, title TEXT NOT NULL,
              media_type TEXT NOT NULL, sha256 TEXT NOT NULL, text TEXT NOT NULL,
              raw BLOB NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(origin,sha256));
            CREATE TABLE IF NOT EXISTS artifacts(
              path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, sources TEXT NOT NULL,
              task_key TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transcript(
              id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, bytes INTEGER NOT NULL);
        """)
        self.db.commit()
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(artifacts)")}
        if "kind" not in columns:
            self.db.execute("ALTER TABLE artifacts ADD COLUMN kind TEXT NOT NULL DEFAULT 'report'")
            self.db.commit()

    def record(self, *, origin, title, text, raw=None, media_type="text/plain"):
        if not all(isinstance(v, str) for v in (origin, title, text, media_type)):
            raise ValidationError("Observation metadata and text must be strings")
        if len(origin) > 8192 or len(title) > 2048:
            raise ValidationError("Observation metadata exceeds limits")
        raw = text.encode("utf-8") if raw is None else raw
        if not isinstance(raw, bytes):
            raise ValidationError("Raw observation must be bytes")
        size = len(raw) + len(text.encode("utf-8"))
        if size > self.max_item_bytes:
            raise ValidationError("Observation exceeds the per-item storage limit")
        sha = hashlib.sha256(raw).hexdigest()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT id FROM sources WHERE origin=? AND sha256=?", (origin, sha)
            ).fetchone()
            if row is None:
                used = self.db.execute(
                    "SELECT coalesce(sum(length(raw)+length(cast(text AS BLOB))),0) FROM sources"
                ).fetchone()[0]
                if used + size > self.max_bytes:
                    raise ValidationError(
                        "Observation storage quota exhausted; no evidence was evicted"
                    )
                cursor = self.db.execute(
                    "INSERT INTO sources(origin,title,media_type,sha256,text,raw) VALUES(?,?,?,?,?,?)",
                    (origin, title, media_type, sha, text, raw),
                )
                ident = cursor.lastrowid
            else:
                ident = row["id"]
        return self.read_source(f"src-{ident:06d}", limit=6000)

    def _row(self, source_id):
        if not isinstance(source_id, str) or not re.fullmatch(r"src-[0-9]{6,12}", source_id):
            raise ValidationError("Invalid source ID")
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM sources WHERE id=?", (int(source_id[4:]),)
            ).fetchone()
        if row is None:
            raise ValidationError("Unknown source ID")
        return row

    def read_source(self, source_id: str, offset: int = 0, limit: int = 6000) -> dict:
        """Read an exact character window of a saved observation; page using next_offset."""
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 24000
        ):
            raise ValidationError("Invalid source window")
        row = self._row(source_id)
        text = row["text"]
        return {
            "source_id": source_id,
            "origin": row["origin"],
            "title": row["title"],
            "sha256": row["sha256"],
            "media_type": row["media_type"],
            "text": text[offset : offset + limit],
            "offset": offset,
            "total_characters": len(text),
            "next_offset": offset + limit if offset + limit < len(text) else None,
        }

    def list_sources(self, offset: int = 0, limit: int = 50) -> dict:
        """List saved observations. Source IDs refer to observed content, not current-world truth."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Invalid source listing window")
        with self.lock:
            rows = self.db.execute(
                "SELECT id,origin,title,sha256,created FROM sources ORDER BY id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            ).fetchall()
        items = [{**dict(r), "source_id": f"src-{r['id']:06d}"} for r in rows[:limit]]
        return {"sources": items, "next_offset": offset + limit if len(rows) > limit else None}

    def record_artifact(self, path, sha, sources, task_key, kind="report"):
        if kind not in {"report", "export", "screenshot", "file"}:
            raise ValidationError("Unknown artifact kind")
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO artifacts(path,sha256,sources,task_key,kind) VALUES(?,?,?,?,?)",
                (path, sha, json.dumps(sources), task_key, kind),
            )

    def artifacts(self, task_key=None):
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM artifacts" + (" WHERE task_key=?" if task_key else ""),
                (task_key,) if task_key else (),
            ).fetchall()
        return [{**dict(r), "sources": json.loads(r["sources"])} for r in rows]

    def close(self):
        self.db.close()

    def event(self, value):
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 32768:
            encoded = json.dumps({"kind": value.get("kind"), "details_omitted": True})
        with self.lock, self.db:
            self.db.execute("INSERT INTO events(payload) VALUES(?)", (encoded,))
            self.db.execute(
                "DELETE FROM events WHERE id <= (SELECT coalesce(max(id),0)-2000 FROM events)"
            )

    def events(self, after=0):
        if type(after) is not int or after < 0:
            raise ValidationError("Invalid event cursor")
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", (after,)
            ).fetchall()
            first = self.db.execute("SELECT min(id) FROM events").fetchone()[0]
        return {
            "events": [{"id": r["id"], **json.loads(r["payload"])} for r in rows],
            "history_truncated": bool(first and after + 1 < first),
            "next_cursor": rows[-1]["id"] if rows else after,
        }

    def transcript_append(self, value):
        """An 8 MiB rolling display log, separate from authoritative receipts/context."""
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
        if size > 32768:
            raise ValidationError("Transcript record exceeds 32 KiB")
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO transcript(payload,bytes) VALUES(?,?)", (encoded, size)
            )
            used = self.db.execute("SELECT coalesce(sum(bytes),0) FROM transcript").fetchone()[0]
            while used > 8388608:
                rows = self.db.execute(
                    "SELECT id,bytes FROM transcript ORDER BY id LIMIT 128"
                ).fetchall()
                self.db.execute("DELETE FROM transcript WHERE id<=?", (rows[-1]["id"],))
                used -= sum(row["bytes"] for row in rows)
            return cursor.lastrowid

    def transcript(self, after=None, limit=20):
        if (
            (after is not None and (type(after) is not int or after < 0))
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValidationError("Invalid transcript cursor or limit")
        with self.lock:
            if after is None:
                rows = list(
                    reversed(
                        self.db.execute(
                            "SELECT * FROM transcript ORDER BY id DESC LIMIT ?", (limit,)
                        ).fetchall()
                    )
                )
            else:
                rows = self.db.execute(
                    "SELECT * FROM transcript WHERE id>? ORDER BY id LIMIT ?", (after, limit)
                ).fetchall()
            first = self.db.execute("SELECT min(id) FROM transcript").fetchone()[0]
            last = self.db.execute("SELECT max(id) FROM transcript").fetchone()[0]
        cursor = rows[-1]["id"] if rows else (after or 0)
        return {
            "records": [{"id": r["id"], **json.loads(r["payload"])} for r in rows],
            "next_cursor": cursor,
            "has_more": bool(last and last > cursor),
            "history_truncated": bool(first and (after or 0) + 1 < first),
            "retention_bytes": 8388608,
        }
