# SPDX-License-Identifier: Apache-2.0
"""Opt-in local workspace tools with bounded I/O and guarded text edits.

Filesystem operations use POSIX descriptor-relative, no-follow operations. They
reject symlink components and protect ``.git``, ``.flora``, and legacy
``.openharness`` state. Optimistic
write checks are serialized among cooperating WorkspaceTools processes via an
advisory root-directory lock; arbitrary editors need not honor that lock.

Commands are a separate explicit capability. ``run_command`` is NOT an operating
system sandbox: a child process has the current user's permissions, environment,
and network access and can access paths outside the workspace.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
import uuid
from pathlib import Path

from flora.integrations.tools import ToolSpec
from flora.support.errors import InterruptedEffect, ValidationError

_PROTECTED = frozenset({".git", ".flora", ".openharness"})
_WRITE_PREFIXES = (".flora-write-", ".openharness-write-")
_PATH_DESCRIPTION = (
    "Relative path inside the selected workspace. No .., absolute paths, "
    "symlinks, .git, .flora, or .openharness."
)
_LEGACY_PATH_DESCRIPTION = (
    "Relative path inside the selected workspace. No .., absolute paths, "
    "symlinks, .git, or .openharness."
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _integer(value, name, minimum=1, maximum=1_000_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


@contextlib.contextmanager
def _publication_guard():
    """Do not report post-publication bookkeeping failures as safe action failures."""
    state = {"published": False}
    try:
        yield state
    except Exception as exc:
        if state["published"]:
            raise InterruptedEffect(
                "File was published but finalization failed; inspect the actual file before continuing"
            ) from exc
        raise


class WorkspaceTools:
    """Ready-to-register local text tools for an explicitly selected directory.

    ``max_file_bytes`` bounds full-file hashing and edits. Reads return bounded
    slices with byte offsets; larger files can be paged but have no full hash.
    ``max_output_bytes`` bounds command bytes and listing/search result payloads.
    ``command_timeout`` is a maximum, not a default tool-registry timeout.
    Non-POSIX systems without descriptor-relative no-follow operations are
    rejected instead of silently weakening path checks.
    """

    def __init__(
        self,
        root,
        *,
        allow_commands=False,
        max_file_bytes=1048576,
        max_output_bytes=65536,
        command_timeout=30,
        protected_paths=(),
    ):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise ValidationError("WorkspaceTools requires POSIX no-follow file operations")
        if type(allow_commands) is not bool:
            raise ValidationError("allow_commands must be a boolean")
        self.max_file_bytes = _integer(max_file_bytes, "max_file_bytes", maximum=8 * 1024 * 1024)
        self.max_output_bytes = _integer(
            max_output_bytes, "max_output_bytes", minimum=1024, maximum=8 * 1024 * 1024
        )
        if type(command_timeout) not in (int, float) or not 0 < command_timeout <= 3600:
            raise ValidationError("command_timeout must be a finite number in (0, 3600]")
        self.command_timeout = float(command_timeout)
        self.allow_commands = allow_commands
        supplied = Path(root).expanduser()
        if supplied.is_symlink():
            raise ValidationError("Workspace root must not be a symlink")
        self.root = supplied.resolve(strict=True)
        if not self.root.is_dir():
            raise ValidationError("Workspace root must be an existing directory")
        root_stat = self.root.stat()
        self._identity = (root_stat.st_dev, root_stat.st_ino)
        if not isinstance(protected_paths, (list, tuple)):
            raise ValidationError("protected_paths must be a sequence of paths")
        self._protected_prefixes = []
        for protected in protected_paths:
            selected = Path(protected).expanduser()
            if not selected.is_absolute():
                selected = self.root / selected
            try:
                relative = selected.resolve(strict=False).relative_to(self.root)
            except ValueError:
                continue  # Session data outside this workspace is already inaccessible.
            self._protected_prefixes.append(tuple(relative.parts))

    def _protected(self, parts):
        return any(tuple(parts[: len(prefix)]) == prefix for prefix in self._protected_prefixes)

    def _parts(self, path, *, directory=False):
        if type(path) is not str or "\0" in path or "\\" in path:
            raise ValidationError("Path must be a relative POSIX path without NUL or backslash")
        if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
            raise ValidationError("Absolute paths are not allowed")
        parts = []
        for part in path.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValidationError("Parent traversal is not allowed")
            if part in _PROTECTED or part.startswith(_WRITE_PREFIXES):
                raise ValidationError(f"Protected workspace component: {part}")
            part.encode("utf-8")
            parts.append(part)
        if len(parts) > 64:
            raise ValidationError("Path exceeds the component limit")
        if self._protected(parts):
            raise ValidationError("Path belongs to protected session state")
        if not parts and not directory:
            raise ValidationError("A file path is required")
        return parts

    @contextlib.contextmanager
    def _directory(self, parts=(), *, write_lock=False):
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = root_fd
        try:
            root_stat = os.fstat(root_fd)
            if (root_stat.st_dev, root_stat.st_ino) != self._identity:
                raise ValidationError(
                    "Workspace root identity changed; create a new WorkspaceTools"
                )
            if write_lock:
                import fcntl

                fcntl.flock(root_fd, fcntl.LOCK_EX)
            for part in parts:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                if current != root_fd:
                    os.close(current)
                current = next_fd
            yield current
        finally:
            if current != root_fd:
                os.close(current)
            os.close(root_fd)

    def _read(self, parent, name, maximum, offset=0):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValidationError("Only regular files can be read")
            if offset > before.st_size:
                raise ValidationError("offset exceeds file size")
            if offset:
                os.lseek(fd, offset, os.SEEK_SET)
            chunks = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(fd)

            # Never issue a write precondition for a file observed while changing.
            def signature(st):
                return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

            if signature(before) != signature(after):
                raise ValidationError("File changed during reading; read it again")
            return b"".join(chunks), after
        finally:
            os.close(fd)

    def read_file(self, path: str, max_bytes: int | None = None, offset: int = 0) -> dict:
        """Read a UTF-8 slice. Full-file hashing is bounded by max_file_bytes.

        A returned full SHA-256 identifies the file revision even when only a
        slice is returned. It does not imply the caller has read all content.
        Files larger than max_file_bytes have sha256=null and cannot be edited
        by this pack without increasing the configured file limit.
        """
        parts = self._parts(path)
        maximum = min(self.max_file_bytes, self.max_output_bytes)
        limit = maximum if max_bytes is None else _integer(max_bytes, "max_bytes", maximum=maximum)
        _integer(offset, "offset", minimum=0, maximum=2**63 - 1)
        with self._directory(parts[:-1]) as parent:
            # The bounded full read yields a truthful revision hash for edits
            # while keeping the text supplied to the model substantially smaller.
            data, info = self._read(parent, parts[-1], self.max_file_bytes)
            complete_hash = (
                hashlib.sha256(data).hexdigest() if len(data) <= self.max_file_bytes else None
            )
            if offset > info.st_size:
                raise ValidationError("offset exceeds file size")
            if complete_hash is not None or offset + limit < len(data):
                selected = data[offset : offset + limit + 1]
            else:
                selected, info = self._read(parent, parts[-1], limit, offset=offset)
        retained = selected[:limit]
        has_more = offset + len(retained) < info.st_size
        # A byte budget can split a valid final UTF-8 character. Omit that tail
        # and return the exact next offset; invalid bytes elsewhere fail closed.
        try:
            content = retained.decode("utf-8")
        except UnicodeDecodeError as exc:
            if has_more and exc.reason == "unexpected end of data" and exc.end == len(retained):
                retained = retained[: exc.start]
                content = retained.decode("utf-8")
                if not retained:
                    raise ValidationError(
                        "max_bytes is too small for the next UTF-8 character; increase it"
                    ) from exc
            else:
                raise ValidationError(
                    "Slice is not UTF-8 text; use next_offset from the previous read"
                ) from exc
        has_more = offset + len(retained) < info.st_size
        truncated = offset > 0 or has_more
        return {
            "path": "/".join(parts),
            "content": content,
            "size_bytes": info.st_size,
            "offset": offset,
            "next_offset": offset + len(retained) if has_more else None,
            "read_bytes": len(retained),
            "truncated": truncated,
            "has_more": has_more,
            "sha256": complete_hash,
            "partial_sha256": hashlib.sha256(retained).hexdigest() if truncated else None,
        }

    def _walk(self, parts, *, recursive, max_depth, visit_limit, stats):
        """Yield safe relative entries without following links; hard-bound visits."""

        def walk(current_parts, depth):
            if stats["visited_entries"] >= visit_limit:
                stats["traversal_truncated"] = True
                return
            with self._directory(current_parts) as directory:
                entries = []
                with os.scandir(directory) as scan:
                    for entry in scan:
                        if stats["visited_entries"] >= visit_limit:
                            stats["traversal_truncated"] = True
                            break
                        stats["visited_entries"] += 1
                        if (
                            entry.name in _PROTECTED
                            or entry.name.startswith(_WRITE_PREFIXES)
                            or self._protected(current_parts + [entry.name])
                        ):
                            stats["skipped_protected"] += 1
                            continue
                        try:
                            entry.name.encode("utf-8")
                            info = entry.stat(follow_symlinks=False)
                        except (UnicodeError, FileNotFoundError):
                            stats["skipped_unavailable"] += 1
                            continue
                        if stat.S_ISLNK(info.st_mode):
                            stats["skipped_symlinks"] += 1
                            continue
                        kind = (
                            "directory"
                            if stat.S_ISDIR(info.st_mode)
                            else "file"
                            if stat.S_ISREG(info.st_mode)
                            else "other"
                        )
                        entries.append((entry.name, kind, info.st_size))
                for name, kind, size in sorted(entries):
                    child = current_parts + [name]
                    yield {"path": "/".join(child), "type": kind, "size_bytes": size}
                    if kind == "directory" and recursive:
                        if depth >= max_depth:
                            stats["skipped_depth"] += 1
                        else:
                            try:
                                yield from walk(child, depth + 1)
                            except (OSError, ValidationError):
                                stats["skipped_unavailable"] += 1

        yield from walk(parts, 0)

    @staticmethod
    def _walk_stats():
        return {
            "visited_entries": 0,
            "skipped_protected": 0,
            "skipped_symlinks": 0,
            "skipped_unavailable": 0,
            "skipped_depth": 0,
            "traversal_truncated": False,
        }

    def list_files(
        self, path: str = ".", recursive: bool = False, max_entries: int = 200, max_depth: int = 8
    ) -> dict:
        """List files deterministically within explicit traversal and output limits."""
        parts = self._parts(path, directory=True)
        if type(recursive) is not bool:
            raise ValidationError("recursive must be a boolean")
        _integer(max_entries, "max_entries", maximum=10000)
        _integer(max_depth, "max_depth", minimum=0, maximum=32)
        stats, entries, output_bytes = self._walk_stats(), [], 0
        truncated = False
        walk = self._walk(
            parts,
            recursive=recursive,
            max_depth=max_depth,
            visit_limit=min(20000, max(1000, max_entries * 20)),
            stats=stats,
        )
        try:
            for entry in walk:
                cost = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
                if len(entries) >= max_entries or output_bytes + cost > self.max_output_bytes - 512:
                    truncated = True
                    break
                entries.append(entry)
                output_bytes += cost
        finally:
            walk.close()
        return {
            "entries": entries,
            "returned_entries": len(entries),
            "truncated": truncated or stats["traversal_truncated"] or stats["skipped_depth"] > 0,
            **stats,
        }

    def search_files(
        self,
        query: str,
        path: str = ".",
        glob: str = "*",
        case_sensitive: bool = True,
        max_matches: int = 100,
        max_files: int = 200,
        max_depth: int = 8,
    ) -> dict:
        """Search literal text in bounded UTF-8 files; line numbers are one-based."""
        parts = self._parts(path, directory=True)
        if (
            type(query) is not str
            or not query
            or len(query.encode("utf-8")) > 4096
            or "\n" in query
        ):
            raise ValidationError("query must be nonempty single-line text of at most 4096 bytes")
        if type(glob) is not str or len(glob) > 1024 or type(case_sensitive) is not bool:
            raise ValidationError("Invalid glob or case_sensitive")
        _integer(max_matches, "max_matches", maximum=10000)
        _integer(max_files, "max_files", maximum=10000)
        _integer(max_depth, "max_depth", minimum=0, maximum=32)
        needle = query if case_sensitive else query.casefold()
        stats, matches, scanned, truncated_files, skipped_binary = self._walk_stats(), [], 0, 0, 0
        truncated, output_bytes = False, 0
        walk = self._walk(
            parts,
            recursive=True,
            max_depth=max_depth,
            visit_limit=min(20000, max(1000, max_files * 20)),
            stats=stats,
        )
        try:
            for entry in walk:
                if entry["type"] != "file" or not fnmatch.fnmatchcase(entry["path"], glob):
                    continue
                if scanned >= max_files:
                    truncated = True
                    break
                scanned += 1
                try:
                    file = self.read_file(entry["path"])
                except (OSError, ValidationError):
                    skipped_binary += 1
                    continue
                if "\0" in file["content"]:
                    skipped_binary += 1
                    continue
                truncated_files += int(file["truncated"])
                for number, line in enumerate(file["content"].splitlines(), 1):
                    if needle not in (line if case_sensitive else line.casefold()):
                        continue
                    encoded_line = line.encode("utf-8")
                    snippet = encoded_line[:4096].decode("utf-8", errors="ignore")
                    match = {
                        "path": entry["path"],
                        "line": number,
                        "text": snippet,
                        "line_truncated": len(encoded_line) > 4096,
                    }
                    cost = len(json.dumps(match, ensure_ascii=False).encode("utf-8"))
                    if (
                        len(matches) >= max_matches
                        or output_bytes + cost > self.max_output_bytes - 768
                    ):
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
            "returned_matches": len(matches),
            "scanned_files": scanned,
            "truncated_files": truncated_files,
            "skipped_nontext_or_unavailable": skipped_binary,
            "truncated": truncated
            or truncated_files > 0
            or stats["traversal_truncated"]
            or stats["skipped_depth"] > 0,
            **stats,
        }

    def _write(self, parts, content, *, expected_sha256=None, create=False, transform=None):
        if type(create) is not bool:
            raise ValidationError("create must be a boolean")
        if create == (expected_sha256 is not None):
            raise ValidationError("Choose create=True or provide expected_sha256, exclusively")
        if expected_sha256 is not None and (
            type(expected_sha256) is not str or not _HASH.fullmatch(expected_sha256)
        ):
            raise ValidationError(
                "expected_sha256 must be the full lowercase SHA-256 from read_file"
            )
        with _publication_guard() as publication, self._directory(
            parts[:-1], write_lock=True
        ) as parent:
            before_hash, mode = None, 0o600
            if not create:
                previous, info = self._read(parent, parts[-1], self.max_file_bytes)
                if len(previous) > self.max_file_bytes:
                    raise ValidationError(
                        "Existing file exceeds max_file_bytes; increase the configured limit"
                    )
                before_hash = hashlib.sha256(previous).hexdigest()
                if before_hash != expected_sha256:
                    raise ValidationError(
                        "File changed since observation: expected_sha256 mismatch"
                    )
                mode = stat.S_IMODE(info.st_mode) & 0o777
                if transform is not None:
                    try:
                        content = transform(previous.decode("utf-8"))
                    except UnicodeDecodeError as exc:
                        raise ValidationError("File is not UTF-8 text") from exc
            if type(content) is not str:
                raise ValidationError("content must be UTF-8 text")
            try:
                data = content.encode("utf-8")
            except UnicodeError as exc:
                raise ValidationError("content must be valid Unicode") from exc
            if len(data) > self.max_file_bytes:
                raise ValidationError("New content exceeds max_file_bytes")
            temporary = ".flora-write-" + uuid.uuid4().hex
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent
            )
            try:
                try:
                    if not create:
                        os.fchmod(fd, mode)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if create:
                        # Atomic no-clobber publish, including when another actor
                        # creates the destination after our initial observation.
                        os.link(
                            temporary,
                            parts[-1],
                            src_dir_fd=parent,
                            dst_dir_fd=parent,
                            follow_symlinks=False,
                        )
                    else:
                        # Recheck immediately before publish. Cooperating writers
                        # are serialized; arbitrary external editors are not.
                        latest, latest_info = self._read(parent, parts[-1], self.max_file_bytes)
                        if (
                            len(latest) > self.max_file_bytes
                            or hashlib.sha256(latest).hexdigest() != expected_sha256
                        ):
                            raise ValidationError(
                                "File changed before publish; no replacement made"
                            )
                        if (latest_info.st_dev, latest_info.st_ino) != (info.st_dev, info.st_ino):
                            raise ValidationError("File identity changed before publish")
                        os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                    publication["published"] = True
                    os.fsync(parent)
                except FileExistsError as exc:
                    raise ValidationError("create=True requires a nonexistent destination") from exc
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        after_hash = hashlib.sha256(data).hexdigest()
        return {
            "path": "/".join(parts),
            "created": create,
            "changed": before_hash != after_hash,
            "before_sha256": before_hash,
            "sha256": after_hash,
            "size_bytes": len(data),
        }

    def write_file(
        self, path: str, content: str, expected_sha256: str | None = None, create: bool = False
    ) -> dict:
        """Atomically publish text; require a matching observed hash or exclusive create."""
        return self._write(
            self._parts(path), content, expected_sha256=expected_sha256, create=create
        )

    def replace_text(
        self, path: str, old: str, new: str, expected_sha256: str, expected_occurrences: int = 1
    ) -> dict:
        """Replace an exact number of literal occurrences in a hash-checked file."""
        if type(old) is not str or not old or type(new) is not str:
            raise ValidationError("old must be nonempty text and new must be text")
        _integer(expected_occurrences, "expected_occurrences", maximum=1000000)

        def transform(previous):
            count = previous.count(old)
            if count != expected_occurrences:
                raise ValidationError(
                    f"Expected {expected_occurrences} occurrences, found {count}; no replacement made"
                )
            return previous.replace(old, new)

        result = self._write(
            self._parts(path), None, expected_sha256=expected_sha256, transform=transform
        )
        return {**result, "replacements": expected_occurrences}

    def run_command(
        self, argv: list[str], cwd: str = ".", timeout_seconds: float | None = None
    ) -> dict:
        """Run argv without a shell. This opt-in capability is NOT an OS sandbox."""
        if not self.allow_commands:
            raise ValidationError("run_command was not enabled for this workspace")
        if (
            type(argv) is not list
            or not 1 <= len(argv) <= 256
            or any(type(arg) is not str or "\0" in arg for arg in argv)
            or not argv[0]
        ):
            raise ValidationError("argv must contain 1 to 256 non-NUL strings")
        if sum(len(arg.encode("utf-8")) for arg in argv) > 128 * 1024:
            raise ValidationError("Command arguments exceed the byte limit")
        timeout = self.command_timeout if timeout_seconds is None else timeout_seconds
        if type(timeout) not in (int, float) or not 0 < timeout <= self.command_timeout:
            raise ValidationError(
                "timeout_seconds must be positive and no greater than command_timeout"
            )
        parts = self._parts(cwd, directory=True)
        start = time.monotonic()
        with self._directory(parts) as directory:
            fd_path = f"/proc/self/fd/{directory}"
            command_cwd = fd_path if os.path.isdir(fd_path) else str(self.root.joinpath(*parts))
            process = subprocess.Popen(
                argv,
                cwd=command_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
                pass_fds=(directory,),
            )
        output = {"stdout": bytearray(), "stderr": bytearray()}
        observed = {"stdout": 0, "stderr": 0}
        retained, timed_out = 0, False
        with selectors.DefaultSelector() as selector:
            for label in output:
                pipe = getattr(process, label)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, label)
            killed_at = None
            try:
                while selector.get_map() or process.poll() is None:
                    now = time.monotonic()
                    if not timed_out and now - start >= timeout:
                        timed_out, killed_at = True, now
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    if killed_at is not None and now - killed_at >= 1:
                        break
                    for key, _ in selector.select(
                        timeout=min(0.05, max(0, timeout - (now - start)))
                        if not timed_out
                        else 0.05
                    ):
                        try:
                            chunk = os.read(key.fileobj.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        label = key.data
                        observed[label] += len(chunk)
                        kept = chunk[: max(0, self.max_output_bytes - retained)]
                        output[label].extend(kept)
                        retained += len(kept)
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
                for label in output:
                    getattr(process, label).close()
        return {
            "argv": argv,
            "cwd": "/".join(parts) or ".",
            "returncode": process.returncode,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - start, 6),
            **{
                label: bytes(data).decode("utf-8", errors="replace")
                for label, data in output.items()
            },
            "stdout_bytes": observed["stdout"],
            "stderr_bytes": observed["stderr"],
            "stdout_truncated": observed["stdout"] > len(output["stdout"]),
            "stderr_truncated": observed["stderr"] > len(output["stderr"]),
            "output_complete": not timed_out,
        }

    def specs(self) -> list[ToolSpec]:
        """Return explicit schemas suitable for Agent(tools=...) or ToolRegistry."""
        text = {"type": "string"}
        path = {
            "type": "string",
            "description": _PATH_DESCRIPTION,
        }
        specs = [
            ToolSpec(
                "list_files",
                self.list_files,
                "List workspace entries. Start here to discover files. Recursive traversal is optional and bounded; inspect truncation and skipped counters.",
                _schema(
                    {
                        "path": path,
                        "recursive": {"type": "boolean"},
                        "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "max_depth": {"type": "integer", "minimum": 0, "maximum": 32},
                    }
                ),
            ),
            ToolSpec(
                "read_file",
                self.read_file,
                "Read a bounded UTF-8 slice; follow next_offset while has_more=true to continue. sha256 identifies the whole observed file when its size fits max_file_bytes, even for a slice; null for larger files. partial_sha256 is never a write precondition. A revision hash does not imply all content was read. Non-regular files are rejected.",
                _schema(
                    {
                        "path": path,
                        "max_bytes": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": min(self.max_file_bytes, self.max_output_bytes),
                        },
                        "offset": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1},
                    },
                    ["path"],
                ),
            ),
            ToolSpec(
                "search_files",
                self.search_files,
                "Search literal text recursively in UTF-8 files. Returns one-based line numbers and explicitly reports incomplete traversal, skipped files and truncated lines. glob matches workspace-relative paths.",
                _schema(
                    {
                        "query": text,
                        "path": path,
                        "glob": text,
                        "case_sensitive": {"type": "boolean"},
                        "max_matches": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "max_files": {"type": "integer", "minimum": 1, "maximum": 10000},
                        "max_depth": {"type": "integer", "minimum": 0, "maximum": 32},
                    },
                    ["query"],
                ),
            ),
            ToolSpec(
                "write_file",
                self.write_file,
                "Write complete UTF-8 content atomically. To edit, first read_file and pass its full expected_sha256. To create, pass create=true without a hash; existing destinations are never overwritten by create. Parent directories must already exist. External editors can race advisory checks.",
                _schema(
                    {
                        "path": path,
                        "content": text,
                        "expected_sha256": {"type": ["string", "null"]},
                        "create": {"type": "boolean"},
                    },
                    ["path", "content"],
                ),
            ),
            ToolSpec(
                "replace_text",
                self.replace_text,
                "Replace literal text only when the complete observed expected_sha256 and exact expected_occurrences both match. On mismatch, read the current file and reconsider the edit. No regex.",
                _schema(
                    {
                        "path": path,
                        "old": text,
                        "new": text,
                        "expected_sha256": text,
                        "expected_occurrences": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 1000000,
                        },
                    },
                    ["path", "old", "new", "expected_sha256"],
                ),
            ),
        ]
        if self.allow_commands:
            specs.append(
                ToolSpec(
                    "run_command",
                    self.run_command,
                    "Run an executable with an argv list, without shell expansion. Explicit capability: NOT an OS sandbox; commands have the user's permissions and can access outside the workspace. Output is bounded and timeouts kill the process group. Use python or an explicitly selected shell only when needed.",
                    _schema(
                        {
                            "argv": {
                                "type": "array",
                                "items": text,
                                "minItems": 1,
                                "maxItems": 256,
                            },
                            "cwd": path,
                            "timeout_seconds": {
                                "type": "number",
                                "minimum": 0.001,
                                "maximum": self.command_timeout,
                            },
                        },
                        ["argv"],
                    ),
                )
            )
        return specs
