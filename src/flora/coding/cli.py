# SPDX-License-Identifier: Apache-2.0
"""Coding task CLI with persistent worktrees and explicit test commands."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from flora.interface.console import _progress
from flora.interface.settings import resolve_settings
from flora.support.errors import ValidationError

from .agent import CodingAgent
from .project import CodingWorkspace, read_json


def add_coding_command(subparsers):
    p = subparsers.add_parser("code", help="Implement a task in a separate Git worktree")
    p.add_argument("task", nargs="?")
    p.add_argument("--repo", help="Clean Git checkout root; required for a new session")
    p.add_argument(
        "--session", required=True, help="Coding session directory outside the source repository"
    )
    p.add_argument("--test", help="Verification command, shell-like quoting but no shell execution")
    p.add_argument("--test-timeout", type=float, default=None)
    p.add_argument(
        "--allow-commands",
        action="store_true",
        default=None,
        help="Also grant arbitrary host command execution in the worktree",
    )
    p.add_argument("--config", help="Provider/compiler/runtime/budget JSON profile")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--api-key-env")
    p.add_argument("--max-output-tokens", type=int)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--json", action="store_true")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument(
        "--status", action="store_true", help="Inspect local state without a model request"
    )
    mode.add_argument(
        "--diff", action="store_true", help="Print the current patch without a model request"
    )


def coding_main(args):
    from flora.interface.cli import _configuration

    if (args.resume or args.status or args.diff) and args.task:
        raise ValidationError("Do not supply a new task together with resume/status/diff")
    if not (args.resume or args.status or args.diff) and not args.task:
        raise ValidationError("Provide a coding task, or select --resume, --status or --diff")
    path = Path(args.session).expanduser() / "coding.json"
    saved = read_json(path) if path.exists() else {}
    if (args.resume or args.status or args.diff) and not saved:
        raise ValidationError("Coding session does not exist")
    project_options = dict(
        session_dir=args.session,
        repo=args.repo,
        test_command=shlex.split(args.test) if args.test else None,
        test_timeout=args.test_timeout
        if args.test_timeout is not None
        else saved.get("test_timeout", 120),
        allow_commands=args.allow_commands
        if args.allow_commands is not None
        else saved.get("allow_commands", False),
    )
    if args.status or args.diff:
        with CodingWorkspace(**project_options) as project:
            print(
                project.patch()
                if args.diff
                else json.dumps(project.coding_status(), ensure_ascii=False, indent=2)
            )
        return 0
    profile = _configuration(args.config)
    provider = profile.get("provider", {}).copy()
    options = resolve_settings(
        model=args.model or provider.pop("model", None),
        base_url=args.base_url or provider.get("base_url"),
        api_key_env=args.api_key_env or provider.get("api_key_env"),
        no_api_key=args.api_key_env is None
        and "api_key_env" in provider
        and provider["api_key_env"] is None,
        allow_insecure_http=provider.get("allow_insecure_http", False),
    )
    model = options.pop("model")
    provider.pop("model", None)
    provider.update(options)
    compiler = profile.get("compiler", {}).copy()
    if args.max_output_tokens is not None:
        compiler["max_output_tokens"] = args.max_output_tokens
    with CodingAgent(
        **project_options,
        model=model,
        provider_options=provider,
        compiler_options=compiler,
        config=profile.get("runtime"),
        budget_limits=profile.get("budget"),
        on_event=None if args.quiet else _progress,
    ) as agent:
        result = agent.resume() if args.resume else agent.run(args.task)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Status: {result.status}\nWorktree: {result.worktree}\nPatch: {result.patch_path}")
        print(
            "Configured checks: "
            + (
                "passed for current changes"
                if result.verification["tests_current_and_passed"]
                else "not verified"
            )
        )
        value = result.result.get("value")
        if value is not None:
            print(json.dumps(value, ensure_ascii=False))
        reason = result.result.get("reason")
        if reason:
            print(f"Reason: {reason}")
        budget = result.result.get("budget", {})
        print(
            f"Usage: {budget.get('model_calls', 0)} model calls, "
            f"{budget.get('tool_calls', 0)} tool calls, "
            f"{budget.get('output_tokens', 0)} reported output tokens"
        )
        if result.status == "needs_program":
            command = ["flora", "code", "--session", args.session, "--resume"]
            for name in ("config", "model", "base_url", "api_key_env", "max_output_tokens"):
                option = getattr(args, name)
                if option is not None:
                    command.extend(["--" + name.replace("_", "-"), str(option)])
            print("Resume with the same saved budget and settled tool receipts:")
            print(shlex.join(command))
        elif result.status == "budget_exhausted":
            print("The saved budget is exhausted. Inspect the patch; resume does not reset usage.")
        elif result.status == "interrupted_unknown":
            print("A tool outcome is unknown. Inspect the saved trace before any manual retry.")
    return 0 if result.status == "completed" else 2
