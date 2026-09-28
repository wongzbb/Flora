# SPDX-License-Identifier: Apache-2.0
"""Directory-scoped session discovery, with no credentials in the registry."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flora.general.agent import read_profile, saved_status
from flora.general.storage import atomic_json
from flora.support.errors import ValidationError

SESSION_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\Z")


def state_home():
    if os.environ.get("FLORA_STATE_HOME"):
        return Path(os.environ["FLORA_STATE_HOME"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Flora"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "flora"


class Sessions:
    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValidationError("Workspace must be a directory")
        digest = hashlib.sha256(os.path.normcase(str(self.workspace)).encode()).hexdigest()
        self.directory = state_home() / "projects" / digest
        if self.directory.is_symlink():
            raise ValidationError("Session registry cannot be a symbolic link")

    def create(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for _ in range(5):
            ident = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
            path = self.directory / ident
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                continue
            now = datetime.now(UTC).isoformat()
            atomic_json(
                path / "terminal.json",
                {
                    "id": ident,
                    "workspace": str(self.workspace),
                    "created": now,
                    "updated": now,
                    "title": "New conversation",
                },
            )
            return path
        raise ValidationError("Could not allocate a unique session ID")

    def records(self):
        result = []
        if not self.directory.exists():
            return result
        for path in self.directory.iterdir():
            if not SESSION_ID.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                row = read_profile(path / "terminal.json")
                if row.get("id") != path.name or row.get("workspace") != str(self.workspace):
                    continue
                if not (path / "general.json").exists():
                    continue
                state = saved_status(path)
                config = read_profile(path / "general.json", max_bytes=1048576)
                result.append(
                    {
                        **row,
                        "path": path,
                        "model": config["profile"].get("provider", {}).get("model", ""),
                        "turns": state["completed_turns"],
                        "status": "unfinished" if state["requires_resume"] else "ready",
                    }
                )
            except (OSError, ValueError, KeyError, TypeError):
                # Keep damaged entries visible; don't silently pretend there is no history.
                result.append(
                    {
                        "id": path.name,
                        "path": path,
                        "title": "Unreadable session",
                        "updated": "",
                        "model": "",
                        "turns": "?",
                        "status": "damaged",
                    }
                )
        return sorted(result, key=lambda r: r.get("updated", ""), reverse=True)

    def select(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f-]{4,32}", ident):
            raise ValidationError(
                "Use a session ID or an unambiguous prefix of at least four characters"
            )
        matches = [
            row for row in self.records() if row["id"] == ident or row["id"].startswith(ident)
        ]
        if len(matches) != 1:
            raise ValidationError(
                "Session ID is missing or ambiguous in this workspace; use --resume to list it"
            )
        if matches[0]["status"] == "damaged":
            raise ValidationError(
                "Session metadata is unreadable; the saved files were left intact"
            )
        return matches[0]["path"]

    def touch(self, path, *, task=None):
        path = Path(path)
        row = read_profile(path / "terminal.json")
        if row.get("workspace") != str(self.workspace) or path.parent != self.directory:
            raise ValidationError("Session does not belong to the selected workspace")
        if task and row["title"] == "New conversation":
            row["title"] = " ".join(task.split())[:120]
        row["updated"] = datetime.now(UTC).isoformat()
        atomic_json(path / "terminal.json", row)
