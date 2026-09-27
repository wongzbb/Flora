# SPDX-License-Identifier: Apache-2.0
"""Task, interactive terminal and local Web entry points."""

from __future__ import annotations

import json
from pathlib import Path

from flora.interface.console import _progress
from flora.interface.settings import resolve_settings
from flora.support.errors import ValidationError

from .agent import GeneralAgent, read_profile, saved_status


def add_general_commands(subparsers):
    for command, help_text in (
        ("agent", "Research, documents and external tools with Flora"),
        ("serve", "Launch the authenticated local Flora Web interface"),
    ):
        p = subparsers.add_parser(command, help=help_text)
        if command == "agent":
            p.add_argument("task", nargs="?")
            mode = p.add_mutually_exclusive_group()
            mode.add_argument("--resume", action="store_true")
            mode.add_argument("--status", action="store_true")
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--port", type=int, default=8765)
        p.add_argument(
            "--workspace", help="Existing workspace directory; required for a new session"
        )
        p.add_argument(
            "--session", required=True, help="Persistent general-agent session directory"
        )
        p.add_argument("--config", help="JSON configuration; saved for subsequent resume")
        p.add_argument("--model")
        p.add_argument("--base-url")
        p.add_argument("--api-key-env")
        p.add_argument("--quiet", action="store_true")


def _profile(args):
    saved = Path(args.session).expanduser() / "general.json"
    if args.config:
        profile = read_profile(args.config)
    elif saved.exists():
        profile = read_profile(saved, max_bytes=1048576)["profile"]
        if not any((args.model, args.base_url, args.api_key_env)):
            return profile
    else:
        profile = {}
    provider = profile.setdefault("provider", {})
    resolved = resolve_settings(
        model=args.model or provider.get("model"),
        base_url=args.base_url or provider.get("base_url"),
        api_key_env=args.api_key_env or provider.get("api_key_env"),
        no_api_key=args.api_key_env is None
        and "api_key_env" in provider
        and provider["api_key_env"] is None,
        allow_insecure_http=provider.get("allow_insecure_http", False),
    )
    provider.update(resolved)
    return profile


def _show(result, as_json=False):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("Status: " + result["status"])
    if result.get("value") is not None:
        value = result["value"]
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    if result.get("reason"):
        print(result["reason"])
    for artifact in result.get("artifacts", []):
        if artifact["current_task"]:
            print("Artifact: " + str(Path(result["workspace"]) / artifact["path"]))
    if result["status"] not in {"completed", "paused"}:
        print(
            "Inspect --status. --resume preserves consumed budgets and settled effects; it does not reset them."
        )


def general_main(args):
    if args.command == "agent" and args.task and (args.resume or args.status):
        raise ValidationError("Do not combine a task with --resume or --status")
    if args.command == "agent" and args.status:
        print(json.dumps(saved_status(args.session), ensure_ascii=False, indent=2))
        return 0
    with GeneralAgent(
        session_dir=args.session,
        workspace=args.workspace,
        profile=_profile(args),
        on_event=None if args.quiet else _progress,
    ) as agent:
        if args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValidationError("Invalid port")
            from .server import serve

            serve(agent, args.port)
            return 0
        if args.status:
            print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
            return 0
        if args.resume or args.task:
            result = agent.resume() if args.resume else agent.run(args.task)
            _show(result, args.json)
            return 0 if result["status"] == "completed" else 2
        if args.json:
            raise ValidationError("--json requires a task, --resume or --status")
        print("Flora · /resume /status /sources /artifacts /exit")
        while True:
            try:
                task = input("You › ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not task:
                continue
            if task == "/exit":
                break
            if task == "/status":
                print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
                continue
            if task == "/sources":
                print(json.dumps(agent.store.list_sources(), ensure_ascii=False, indent=2))
                continue
            if task == "/artifacts":
                print(json.dumps(agent.artifact_status(), ensure_ascii=False, indent=2))
                continue
            try:
                _show(agent.resume() if task == "/resume" else agent.run(task))
            except ValidationError as exc:
                print(str(exc))
        return 0
