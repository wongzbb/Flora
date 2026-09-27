# SPDX-License-Identifier: Apache-2.0
"""Explicit, content-addressed task guides. A guide cannot grant capabilities."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from flora.support.errors import ValidationError

from .documents import DocumentWorkspace


class SkillCatalog:
    def __init__(self, directories=()):
        self.entries = {}
        for selected in directories:
            root = Path(selected).expanduser().resolve(strict=True)
            files = DocumentWorkspace(root)
            listing = files.list_files(recursive=True, max_entries=1000, max_depth=16)
            if (
                listing["truncated"]
                or listing["skipped_symlinks"]
                or listing["skipped_unavailable"]
            ):
                raise ValidationError(
                    "Skill directory is incomplete, oversized or contains symlinks"
                )
            for entry in listing["entries"]:
                if entry["type"] != "file" or not entry["path"].endswith(".md"):
                    continue
                if len(self.entries) >= 100:
                    raise ValidationError("Skill catalog exceeds 100 documents")
                relative = entry["path"]
                # Descriptor-relative reads reject symlinks even when a parent directory changes.
                raw = files.read_bytes(relative)
                if len(raw) > 65536:
                    raise ValidationError("Skill document exceeds 64 KiB: " + relative)
                content = raw.decode("utf-8")
                name = re.sub(r"[^A-Za-z0-9_.-]", "-", root.name + "-" + relative[:-3])
                if name in self.entries:
                    raise ValidationError("Duplicate skill name: " + name)
                self.entries[name] = {
                    "name": name,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content": content,
                    "description": next(
                        (x.strip("# ") for x in content.splitlines() if x.strip()), name
                    )[:300],
                }

    def manifest(self):
        return [{k: v for k, v in x.items() if k != "content"} for x in self.entries.values()]

    def list_skills(self) -> dict:
        """List explicitly installed task guides; guides do not add tool permissions."""
        return {"skills": self.manifest()}

    def read_skill(self, name: str) -> dict:
        """Read a task guide from the immutable catalog loaded for this session."""
        if name not in self.entries:
            raise ValidationError("Unknown skill")
        return dict(self.entries[name])
