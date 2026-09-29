# SPDX-License-Identifier: Apache-2.0
"""Opt-in installed CLI, continuous-session smoke test using the plain input mode.

No imported GeneralAgent execution: invokes the actual installed executable in a
fresh workspace with an isolated state home. Secret input is pipe-only, not an
argument or environment variable. POSIX only; bounded by per-prompt deadlines.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import select
import shutil
import subprocess
import time
from pathlib import Path


def assess_turn(name, result, workspace, *, original):
    """Fixture-specific checks, not a semantic verifier or an alternate agent path."""
    value = result.get("value")
    text = json.dumps(value, ensure_ascii=False)
    observations = [e for e in result.get("reports", []) if e.get("kind") == "tool_result"]
    tools = [e.get("tool") for e in observations]
    statuses = [e.get("status") for e in observations]
    if result.get("status") != "completed":
        return False
    if name in ("greet", "greet_again"):
        return (
            isinstance(value, str)
            and bool(re.search(r"hello|hi\b|你好|您好", value, re.I))
            and not tools
        )
    if name == "path":
        return (
            tools == ["workspace_context"] and statuses == ["returned"] and str(workspace) in text
        )
    if name == "read":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return False
        return (
            tools == ["read_file"]
            and statuses == ["returned"]
            and isinstance(value, dict)
            and set(value) == {"project", "total"}
            and value["project"] == "CLI continuity fixture"
            and type(value["total"]) in (int, float)
            and value["total"] == 4
        )
    if name == "edit":
        path = workspace / "notes.txt"
        return (
            tools in (["read_file", "append_lines"], ["read_file", "update_file"])
            and statuses == ["returned", "returned"]
            and "notes.txt" in text
            and path.is_file()
            and path.read_bytes() in (original + b"AUDITED", original + b"AUDITED\n")
        )
    if name == "readback":
        path = workspace / "notes.txt"
        return (
            tools == ["read_file"]
            and statuses == ["returned"]
            and path.is_file()
            and value == path.read_text()
        )
    if name == "conditional":
        return (
            tools == ["read_file", "read_file"]
            and statuses == ["raised", "returned"]
            and value == "CLI continuity fixture"
            and not (workspace / "optional.json").exists()
        )
    raise ValueError("unknown CLI probe case")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", default="flora")
    parser.add_argument("--cases", help="Comma-separated subset; default is all continuous turns")
    args = parser.parse_args()
    executable = shutil.which(args.executable)
    if executable is None:
        parser.error("installed flora executable not found")
    key = getpass.getpass("API key (hidden; memory only): ")
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    evidence = '{"project":"CLI continuity fixture","records":[{"count":6},{"count":-2}]}'
    original = b"Initial public note.\n"
    (workspace / "evidence.json").write_text(evidence)
    (workspace / "notes.txt").write_bytes(original)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)  # Do not accidentally test an uninstalled source override.
    environment.update(FLORA_STATE_HOME=str(output / "state"), PYTHONUNBUFFERED="1")
    command = [executable, "--plain", "--workspace", str(workspace)]
    if args.profile:
        command.extend(["--config", str(args.profile.resolve())])
    process = subprocess.Popen(
        command,
        cwd=workspace,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    pending = b""
    transcript = bytearray()
    rows = []

    def save(path, value):
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2).replace(key, "[REDACTED]") + "\n"
        )

    def wait_for(marker, timeout=150):
        nonlocal pending
        marker = marker.encode()
        deadline = time.monotonic() + timeout
        while marker not in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("installed CLI prompt deadline exceeded")
            ready, _, _ = select.select([process.stdout], [], [], min(1, remaining))
            if not ready:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("installed CLI ended before its next prompt")
            pending += chunk
            transcript.extend(chunk)
            if len(transcript) > 32 * 1024 * 1024:
                raise RuntimeError("installed CLI output limit exceeded")
        position = pending.index(marker) + len(marker)
        result, pending = pending[:position], pending[position:]
        return result

    def send(value):
        process.stdin.write((value + "\n").encode())
        process.stdin.flush()

    try:
        wait_for("Base URL › ")
        send(args.base_url)
        wait_for("API Key › ")
        send(key)
        wait_for("Model · number or full ID › ")
        send(args.model)
        wait_for("flora › ")
        turns = [
            ("greet", "hello"),
            ("path", "你当前位于什么路径"),
            (
                "read",
                "读取 evidence.json，只返回 JSON 对象，project 为原 project 字段，total 为 records 中 count 的总和。",
            ),
            (
                "edit",
                "读取 notes.txt，保留原有内容并在末尾追加一行 AUDITED，直接更新原文件并告诉我路径。",
            ),
            ("readback", "重新读取 notes.txt，只返回它的完整原文。"),
            (
                "conditional",
                "先尝试读取 optional.json；若它不存在，改为读取 evidence.json 并只返回其 project 字段。不要创建任何文件。",
            ),
            ("greet_again", "hello"),
        ]
        if args.cases:
            names = args.cases.split(",")
            available = dict(turns)
            if any(name not in available for name in names):
                raise ValueError("unknown CLI probe case")
            turns = [(name, available[name]) for name in names]
        previous_calls = 0
        for index, (name, task) in enumerate(turns, 1):
            started = time.monotonic()
            send(task)
            wait_for("flora › ")
            elapsed = round(time.monotonic() - started, 3)
            sessions = list((output / "state").glob("projects/*/*/result.json"))
            if len(sessions) != 1:
                raise RuntimeError("expected exactly one saved CLI session")
            result = json.loads(sessions[0].read_text())
            state = json.loads((sessions[0].parent / "kernel/session.json").read_text())
            tools = [e["tool"] for e in result.get("reports", []) if e.get("kind") == "tool_result"]
            passed = assess_turn(name, result, workspace, original=original)
            passed = (
                passed
                and state["completed_turns"] == index
                and (workspace / "evidence.json").read_text() == evidence
            )
            calls = result["budget"]["model_calls"]
            row = {
                "case": name,
                "task": task,
                "elapsed_seconds": elapsed,
                "passed": passed,
                "model_calls_this_turn": calls - previous_calls,
                "tools": tools,
                "result": result,
                "completed_turns": state["completed_turns"],
            }
            previous_calls = calls
            rows.append(row)
            save(output / "results.json", rows)
            print(
                json.dumps(
                    {k: v for k, v in row.items() if k != "result"}, ensure_ascii=False
                ).replace(key, "[REDACTED]"),
                flush=True,
            )
            if not passed:
                break  # Preserve the failure; do not mis-submit new tasks to an unfinished one.
        send("/exit")
        process.wait(timeout=15)
        save(
            output / "manifest.json",
            {
                "command": command,
                "input_mode": "plain CLI through pipes",
                "PYTHONPATH": "removed",
                "exit_code": process.returncode,
                "all_passed": len(rows) == len(turns) and all(r["passed"] for r in rows),
            },
        )
    except Exception as exc:
        save(output / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        raise SystemExit("CLI probe failed; inspect redacted error.json") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()
        (output / "terminal.txt").write_bytes(
            bytes(transcript).replace(key.encode(), b"[REDACTED]")
        )
        leaks = [
            str(p.relative_to(output))
            for p in output.rglob("*")
            if p.is_file() and key.encode() in p.read_bytes()
        ]
        print(
            json.dumps({"phase": "credential_scan", "saved_secret": bool(leaks), "files": leaks}),
            flush=True,
        )
        key = None


if __name__ == "__main__":
    main()
