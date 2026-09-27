# SPDX-License-Identifier: Apache-2.0
"""Persistent coding workspaces and revision-bound, observable test evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from flora.integrations.workspace import WorkspaceTools
from flora.support.errors import ValidationError

try:
    import fcntl
except ImportError:  # Keep non-workspace CLI commands importable on Windows.
    fcntl = None


def write_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".coding-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValidationError("Invalid coding state file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValidationError("Coding state must be an object")
    return value


class CodingWorkspace:
    """A detached worktree, not an operating-system security sandbox.

    The session directory must be outside the source checkout. Its lock is held
    for the lifetime of this object; external Git editors must coordinate too.
    """

    def __init__(
        self, session_dir, *, repo=None, test_command=None, test_timeout=120, allow_commands=False
    ):
        if os.name != "posix" or fcntl is None:
            raise ValidationError("Coding workspaces require POSIX; use WSL on Windows")
        supplied = Path(session_dir).expanduser()
        if supplied.is_symlink():
            raise ValidationError("Coding session must not be a symlink")
        if repo is not None and supplied.resolve().is_relative_to(
            Path(repo).expanduser().resolve()
        ):
            raise ValidationError("Place the coding session outside the source repository")
        self.directory = supplied.absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory = self.directory.resolve()
        self._lock = None
        self._closed = False
        lockpath = self.directory / ".coding.lock"
        fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self._lock = os.fdopen(fd, "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock.close()
            self._lock = None
            raise ValidationError("Coding session is already in use") from None
        try:
            self._open(repo, test_command, test_timeout, allow_commands)
        except BaseException:
            self.close()
            raise

    def _open(self, repo, test_command, test_timeout, allow_commands):
        manifest = self.directory / "coding.json"
        if manifest.exists():
            self.config = read_json(manifest)
            if self.config.get("format") != "flora-coding-v1":
                raise ValidationError("Unsupported coding session format")
            if repo is not None and str(Path(repo).resolve()) != self.config["repo"]:
                raise ValidationError("Coding session belongs to a different repository")
            if test_command is not None and test_command != self.config["test_command"]:
                raise ValidationError("Test command differs from the saved coding session")
            if (
                test_timeout != self.config["test_timeout"]
                or allow_commands != self.config["allow_commands"]
            ):
                raise ValidationError("Reopen with the saved timeout and command permissions")
        else:
            if repo is None:
                raise ValidationError("A new coding session requires --repo")
            if (
                not isinstance(test_command, list)
                or not 1 <= len(test_command) <= 256
                or any(not isinstance(s, str) or "\0" in s for s in test_command)
                or not test_command[0]
            ):
                raise ValidationError("Provide a nonempty test command as an argv list")
            if sum(len(s.encode()) for s in test_command) > 128 * 1024:
                raise ValidationError("Test command is too large")
            if type(test_timeout) not in (int, float) or not 0 < test_timeout <= 3600:
                raise ValidationError("Test timeout must be in (0, 3600] seconds")
            if type(allow_commands) is not bool:
                raise ValidationError("allow_commands must be a boolean")
            source = Path(repo).expanduser().resolve(strict=True)
            top = self._git(source, "rev-parse", "--show-toplevel").strip()
            if str(source) != top:
                raise ValidationError("--repo must be the Git checkout root")
            if self.directory == source or source in self.directory.parents:
                raise ValidationError("Place the coding session outside the source repository")
            if self._git(source, "status", "--porcelain", "--untracked-files=all").strip():
                raise ValidationError(
                    "Source checkout is dirty; preserve your changes before starting a new coding session"
                )
            base = self._git(source, "rev-parse", "--verify", "HEAD").strip()
            entries = self._git(source, "ls-files", "--stage")
            if entries.startswith("160000 ") or "\n160000 " in entries:
                raise ValidationError(
                    "Submodule repositories require a separately prepared environment"
                )
            worktree = self.directory / "worktree"
            if worktree.exists():
                raise ValidationError(
                    "Unregistered worktree already exists; inspect it before creating this session"
                )
            self._git(source, "worktree", "add", "--detach", str(worktree), base)
            self.config = {
                "format": "flora-coding-v1",
                "repo": str(source),
                "base_commit": base,
                "test_command": test_command,
                "test_timeout": test_timeout,
                "allow_commands": allow_commands,
            }
            write_json(manifest, self.config)
        self.root = self.directory / "worktree"
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValidationError("Coding worktree is missing or replaced")
        source = Path(self.config["repo"])
        if self.directory == source or source in self.directory.parents:
            raise ValidationError("Coding session cannot be inside the source repository")
        if self._git(self.root, "rev-parse", "HEAD").strip() != self.config["base_commit"]:
            raise ValidationError(
                "Worktree HEAD changed; commits and resets are not part of this session"
            )
        actual_common = Path(self._git(self.root, "rev-parse", "--git-common-dir").strip())
        expected_common = Path(self._git(source, "rev-parse", "--git-common-dir").strip())
        if (self.root / actual_common).resolve() != (source / expected_common).resolve():
            raise ValidationError("Worktree is attached to a different repository")
        self.files = WorkspaceTools(
            self.root, allow_commands=allow_commands, command_timeout=test_timeout
        )
        self.test_runner = WorkspaceTools(
            self.root, allow_commands=True, command_timeout=test_timeout, max_output_bytes=131072
        )

    @staticmethod
    def _git(root, *args, diff=False):
        # The existing bounded command runner handles timeouts/process groups.
        result = WorkspaceTools(
            root, allow_commands=True, max_output_bytes=4 * 1024 * 1024
        ).run_command(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args]
        )
        if (
            result["timed_out"]
            or result["stdout_truncated"]
            or result["stderr_truncated"]
            or result["returncode"] not in ((0, 1) if diff else (0,))
        ):
            raise ValidationError(
                "Git operation failed or exceeded its output/time limit: " + result["stderr"][:512]
            )
        if len(result["stdout"].encode()) != result["stdout_bytes"]:
            raise ValidationError(
                "Non-UTF-8 diff/path output is not supported; no lossy patch is exported"
            )
        return result["stdout"]

    def patch(self):
        if self._git(self.root, "rev-parse", "HEAD").strip() != self.config["base_commit"]:
            raise ValidationError("Worktree HEAD changed; refusing to hide committed changes")
        text = self._git(
            self.root,
            "diff",
            "--no-color",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            self.config["base_commit"],
            "--",
        )
        untracked = self._git(self.root, "ls-files", "--others", "--exclude-standard", "-z")
        for name in sorted(filter(None, untracked.split("\0"))):
            path = self.root / name
            if not path.parent.resolve().is_relative_to(self.root.resolve()):
                raise ValidationError("Untracked path escapes the coding worktree")
            text += self._git(
                self.root,
                "diff",
                "--no-color",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                "--no-index",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--",
                "/dev/null",
                name,
                diff=True,
            )
            if len(text.encode()) > 4 * 1024 * 1024:
                raise ValidationError("Coding patch exceeds 4 MiB")
        return text

    def revision(self):
        return hashlib.sha256(
            (self.config["base_commit"] + "\n" + self.patch()).encode()
        ).hexdigest()

    def run_tests(self) -> dict:
        """Run the configured test command. Inspect failures and edit before retrying.

        Passed means exit code zero, no timeout and the reviewed file revision
        stayed unchanged during the check. This does not prove task correctness.
        """
        before = self.revision()
        path = self.directory / "test-result.json"
        write_json(path, {"status": "running", "revision": before})
        result = self.test_runner.run_command(self.config["test_command"])
        after = self.revision()
        report = {
            "status": "passed"
            if result["returncode"] == 0 and not result["timed_out"] and before == after
            else "failed",
            "revision": after,
            "files_changed_during_check": before != after,
            **result,
        }
        write_json(path, report)
        return report

    def coding_status(self) -> dict:
        """Read current change revision and whether tests cover this exact revision."""
        revision = self.revision()
        path = self.directory / "test-result.json"
        evidence = read_json(path) if path.exists() else None
        current = bool(
            evidence and evidence.get("status") == "passed" and evidence.get("revision") == revision
        )
        return {
            "worktree": str(self.root),
            "base_commit": self.config["base_commit"],
            "test_command": self.config["test_command"],
            "revision": revision,
            "tests_current_and_passed": current,
            "last_test": evidence,
            "verification_scope": "configured command and Git-visible file revision; not task correctness",
        }

    def inspect_changes(self) -> dict:
        """Read the working changes, including new files; no commits or pushes occur."""
        patch = self.patch()
        return {
            "patch": patch[:65536],
            "truncated": len(patch) > 65536,
            "patch_bytes": len(patch.encode()),
            "revision": self.revision(),
        }

    def complete(self):
        return self.coding_status()["tests_current_and_passed"]

    def export(self):
        patch = self.patch()
        path = self.directory / "changes.patch"
        if path.is_symlink():
            raise ValidationError("Patch export target must not be a symlink")
        path.write_text(patch)
        return str(path)

    def close(self):
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
