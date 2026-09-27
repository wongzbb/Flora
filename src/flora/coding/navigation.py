# SPDX-License-Identifier: Apache-2.0
"""Bounded code navigation, sharing the existing workspace path protections."""

from __future__ import annotations

import fnmatch
import hashlib
import json

from flora.integrations.binding import make_registry
from flora.integrations.workspace import WorkspaceTools, _integer
from flora.support.errors import ValidationError


class CodingFiles(WorkspaceTools):
    """Read full bounded files locally; return only relevant source windows."""

    def _source(self, path):
        parts = self._parts(path)
        with self._directory(parts[:-1]) as parent:
            data, _ = self._read(parent, parts[-1], self.max_file_bytes)
        if len(data) > self.max_file_bytes:
            raise ValidationError("Source exceeds the 1 MiB navigation limit")
        if b"\0" in data:
            raise ValidationError("Source is not a UTF-8 text file")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationError("Source is not a UTF-8 text file") from None
        return content, hashlib.sha256(data).hexdigest(), len(data)

    def read_lines(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        """Read a one-based inclusive source window, default 60 and at most 200 lines.

        Returns at most 16 KiB of source with a full-file SHA-256 for edits.
        Use search_code to locate relevant lines. Huge single lines are rejected;
        use read_file byte slices for them. A window is not a full file read.
        """
        _integer(start_line, "start_line", maximum=2**31 - 1)
        if end_line is None:
            end_line = start_line + 59
        _integer(end_line, "end_line", minimum=start_line, maximum=start_line + 199)
        content, digest, size = self._source(path)
        lines = content.splitlines(keepends=True)
        if start_line > len(lines) + 1:
            raise ValidationError("start_line exceeds file length")
        selected, used = [], 0
        for line in lines[start_line - 1 : end_line]:
            length = len(line.encode("utf-8"))
            if used + length > 16384:
                if not selected:
                    raise ValidationError("Line exceeds 16 KiB; use read_file with byte offsets")
                break
            selected.append(line)
            used += length
        last_line = start_line + len(selected) - 1
        return {
            "path": path,
            "content": "".join(selected),
            "start_line": start_line,
            "end_line": last_line,
            "total_lines": len(lines),
            "next_line": last_line + 1 if last_line < len(lines) else None,
            "sha256": digest,
            "size_bytes": size,
            "truncated": start_line > 1 or last_line < len(lines),
        }

    def search_code(
        self,
        query: str,
        path: str = ".",
        glob: str = "*",
        case_sensitive: bool = True,
        max_matches: int = 30,
        max_files: int = 200,
    ) -> dict:
        """Find literal text anywhere in UTF-8 files up to 1 MiB each.

        Returns matching line numbers; follow with read_lines for source context.
        Scan budget is 8 MiB and output budget 16 KiB. Check truncation/skipped
        counts before concluding that text is absent. glob is workspace-relative.
        """
        parts = self._parts(path, directory=True)
        if type(query) is not str or not query or "\n" in query or len(query.encode()) > 4096:
            raise ValidationError("query must be single-line nonempty text of at most 4096 bytes")
        if type(glob) is not str or len(glob) > 1024 or type(case_sensitive) is not bool:
            raise ValidationError("Invalid glob or case_sensitive")
        _integer(max_matches, "max_matches", maximum=200)
        _integer(max_files, "max_files", maximum=2000)
        needle = query if case_sensitive else query.casefold()
        stats = self._walk_stats()
        matches, scanned, scanned_bytes, skipped, output_bytes = [], 0, 0, 0, 0
        truncated = False
        walk = self._walk(parts, recursive=True, max_depth=16, visit_limit=20000, stats=stats)
        try:
            for entry in walk:
                if entry["type"] != "file" or not fnmatch.fnmatchcase(entry["path"], glob):
                    continue
                if scanned >= max_files:
                    truncated = True
                    break
                if entry["size_bytes"] > self.max_file_bytes:
                    skipped += 1
                    scanned += 1
                    continue
                # Reserve the remaining read budget using actual bounded bytes,
                # not a possibly stale directory-entry size.
                if scanned_bytes + self.max_file_bytes + 1 > 8 * 1024 * 1024:
                    truncated = True
                    break
                scanned += 1
                try:
                    content, digest, size = self._source(entry["path"])
                except (OSError, ValidationError):
                    skipped += 1
                    scanned_bytes += self.max_file_bytes + 1
                    continue
                scanned_bytes += size
                for number, line in enumerate(content.splitlines(), 1):
                    if needle not in (line if case_sensitive else line.casefold()):
                        continue
                    raw = line.encode("utf-8")
                    match = {
                        "path": entry["path"],
                        "line": number,
                        "text": raw[:1024].decode("utf-8", errors="ignore"),
                        "line_truncated": len(raw) > 1024,
                        "sha256": digest,
                    }
                    cost = len(json.dumps(match, ensure_ascii=False).encode("utf-8"))
                    if len(matches) >= max_matches or output_bytes + cost > 15000:
                        truncated = True
                        break
                    matches.append(match)
                    output_bytes += cost
                if truncated:
                    break
        finally:
            walk.close()
        return {
            "matches": matches,
            "scanned_files": scanned,
            "scanned_bytes": scanned_bytes,
            "skipped_nontext_oversize_or_unavailable": skipped,
            "truncated": truncated
            or skipped > 0
            or stats["traversal_truncated"]
            or stats["skipped_depth"] > 0,
            **stats,
        }

    def specs(self):
        return super().specs() + list(
            make_registry([self.read_lines, self.search_code])._tools.values()
        )
