# SPDX-License-Identifier: Apache-2.0
"""Natural-language command and interactive console, with IR kept internal."""

from __future__ import annotations

import importlib
import json
import sys

from flora.integrations.adapters import AgentEnvBridge
from flora.interface.settings import resolve_settings, save_settings
from flora.support.errors import FloraError, ValidationError


def add_direct_commands(subparsers) -> None:
    setup = subparsers.add_parser("setup", help="Save the model and nonsecret connection settings")
    setup.add_argument("--model", required=True)
    setup.add_argument("--base-url")
    setup_auth = setup.add_mutually_exclusive_group()
    setup_auth.add_argument(
        "--api-key-env", help="Environment variable NAME; never the API key itself"
    )
    setup_auth.add_argument(
        "--no-api-key",
        action="store_true",
        help="Send no Authorization header (for local/unauthenticated servers)",
    )
    for name, help_text in (
        ("ask", "Give the agent a natural-language task"),
        ("chat", "Start an interactive natural-language session"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        if name == "ask":
            command.add_argument("task", help="Natural-language task; quote it as one argument")
            command.add_argument(
                "--json", action="store_true", help="Print the complete RunResult JSON"
            )
        command.add_argument("--model", help="Model name; overrides environment and saved settings")
        command.add_argument(
            "--base-url", help="OpenAI-compatible API base URL, usually ending in /v1"
        )
        auth = command.add_mutually_exclusive_group()
        auth.add_argument("--api-key-env", help="Environment variable NAME containing the API key")
        auth.add_argument(
            "--no-api-key",
            action="store_true",
            help="Send no Authorization header (for local/unauthenticated servers)",
        )
        command.add_argument("--workspace", help="Enable file tools rooted in this directory")
        command.add_argument(
            "--config", help="JSON provider/compiler/runtime/budget profile; explicit flags override it"
        )
        command.add_argument(
            "--allow-commands",
            action="store_true",
            help="Also enable command execution; requires --workspace",
        )
        command.add_argument(
            "--session", help="Persist this session in a directory; omit for an in-memory session"
        )
        command.add_argument(
            "--tools", help="Trusted Python module:factory returning tools (optional)"
        )
        command.add_argument(
            "--max-output-tokens", type=int, help="Maximum output tokens per model call"
        )
        command.add_argument("--quiet", action="store_true", help="Suppress progress notices")


def _factory(spec: str):
    if ":" not in spec:
        raise ValidationError("--tools requires an explicit trusted module:factory")
    module, name = spec.split(":", 1)
    if not module or not name.isidentifier() or name.startswith("_"):
        raise ValidationError("Invalid --tools factory name")
    factory = getattr(importlib.import_module(module), name)
    if not callable(factory):
        raise ValidationError("--tools factory must be callable")
    return factory()


def _progress(event: dict) -> None:
    """Display only event types, never raw model content, tool arguments or outputs."""
    if not isinstance(event, dict):
        return
    kind = event.get("kind", event.get("type", event.get("event", "")))
    descriptions = {
        "model_call_started": "Planning…",
        "action_selected": "Running a tool…",
        "replan_requested": "Replanning from the observed results…",
        "checkpoint_restored": "Resuming the saved task…",
        "model_start": "Planning…",
        "model_call": "Planning…",
        "model_request": "Planning…",
        "compile_start": "Planning…",
        "tool_start": "Running a tool…",
        "tool_call": "Running a tool…",
        "effect_begin": "Running a tool…",
        "effect_start": "Running a tool…",
        "resuming": "Resuming the saved task…",
        "turn_start": "Working…",
        "run_start": "Working…",
    }
    message = descriptions.get(kind)
    if message:
        print(f"[Flora] {message}", file=sys.stderr, flush=True)


def _agent(args):
    from flora.agent.api import Agent
    from flora.interface.cli import _configuration

    profile = _configuration(args.config)
    provider_options = profile.get("provider", {}).copy()
    # An explicitly supplied profile overrides saved/environment settings;
    # direct command flags override the corresponding profile fields.
    options = resolve_settings(
        model=args.model if args.model is not None else provider_options.pop("model", None),
        base_url=args.base_url if args.base_url is not None else provider_options.get("base_url"),
        api_key_env=args.api_key_env if args.api_key_env is not None else provider_options.get("api_key_env"),
        no_api_key=args.no_api_key or (args.api_key_env is None and
                   "api_key_env" in provider_options and provider_options["api_key_env"] is None),
        allow_insecure_http=provider_options.get("allow_insecure_http", False),
    )
    model = options.pop("model")
    provider_options.pop("model", None)
    provider_options.update(options)
    tools = _factory(args.tools) if args.tools else None
    extra = {}
    if isinstance(tools, AgentEnvBridge):
        extra = {"instructions": tools.instructions, "completion_guard": tools.completion_guard}
        tools = tools.registry
    compiler_options = profile.get("compiler", {}).copy()
    if args.max_output_tokens is not None:
        compiler_options["max_output_tokens"] = args.max_output_tokens
    return Agent(
        model=model,
        provider_options=provider_options,
        compiler_options=compiler_options,
        config=profile.get("runtime"),
        budget_limits=profile.get("budget"),
        tools=tools,
        workspace=args.workspace,
        allow_commands=args.allow_commands,
        session_dir=args.session,
        on_event=None if args.quiet else _progress,
        **extra,
    )


def _print_result(result, *, as_json: bool = False, resume_hint: str | None = None) -> int:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
    elif result.status == "completed":
        print(
            result.value
            if isinstance(result.value, str)
            else json.dumps(result.value, ensure_ascii=False, indent=2, allow_nan=False)
        )
    else:
        print(f"Task stopped: {result.status}. {result.reason}", file=sys.stderr)
    if result.status != "completed":
        print(
            resume_hint
            or "The task is unfinished. Use /status and /resume. Resolve an unknown tool outcome before resuming it.",
            file=sys.stderr,
        )
        return 2
    return 0


_HELP = """Enter a task in natural language. Internal programs and contracts are managed automatically.
/help    Show this help
/status  Show session state and consumed budget
/resume  Continue the unfinished task, without submitting a new task
/quit    Close the session (also /exit)
Every unfinished task must be resumed before submitting another task.
File tools require --workspace DIR. Command execution additionally requires --allow-commands.
Use --session DIR to save turns and recover after exiting."""


def _chat(args) -> int:
    with _agent(args) as agent:
        if not args.quiet:
            storage = (
                f"saved session: {agent.session_dir}" if agent.session_dir else "in-memory session"
            )
            print(f"Flora — {storage}. Enter a task; /help lists commands.", file=sys.stderr)
            if args.workspace:
                commands = "enabled" if args.allow_commands else "disabled"
                print(f"Workspace: {args.workspace}; commands {commands}.", file=sys.stderr)
            else:
                print("No workspace selected; use --workspace DIR for file tasks.", file=sys.stderr)
            if agent.status().get("requires_resume"):
                print("An unfinished task is saved. Enter /resume to continue it.", file=sys.stderr)
        interactive = sys.stdin.isatty()
        while True:
            try:
                if interactive:
                    print("flora> ", end="", file=sys.stderr, flush=True)
                line = sys.stdin.readline()
                if line == "":
                    return 0
                task = line.strip()
                if not task:
                    continue
                if task in {"/quit", "/exit"}:
                    return 0
                if task == "/help":
                    print(_HELP)
                elif task == "/status":
                    print(json.dumps(agent.status(), ensure_ascii=False, indent=2, allow_nan=False))
                elif task == "/resume":
                    _print_result(agent.resume())
                elif task.startswith("/"):
                    print("Unknown command. Enter /help for available commands.", file=sys.stderr)
                else:
                    _print_result(agent.run(task))
            except KeyboardInterrupt:
                # Do not automatically resubmit an interrupted action or model request.
                print(
                    "\nInterrupted. No automatic retry was made. Enter /status, /resume, or /quit.",
                    file=sys.stderr,
                )
                if not interactive:
                    return 130
            except (FloraError, ValueError, TypeError, OSError) as exc:
                print(f"Flora: {exc}", file=sys.stderr)


def direct_main(args) -> int:
    if args.command == "setup":
        path = save_settings(
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            no_api_key=args.no_api_key,
        )
        print(
            f"Saved model settings to {path}. API keys are read from environment variables and are never saved."
        )
        return 0
    if args.command == "chat":
        return _chat(args)
    with _agent(args) as agent:
        try:
            hint = (
                "The task is unfinished. Reopen chat with the same --session directory and connection/tool options, then enter /status or /resume. Resolve an unknown tool outcome before resuming it."
                if args.session
                else "The task is unfinished. This in-memory run cannot be resumed after exit. Select --session DIR when recovery is needed."
            )
            return _print_result(agent.run(args.task), as_json=args.json, resume_hint=hint)
        except KeyboardInterrupt:
            message = "Interrupted. No automatic retry was made. "
            message += (
                "Reopen chat with the same --session directory to inspect and resume saved work."
                if args.session
                else "This in-memory session cannot be recovered after exit."
            )
            print(message, file=sys.stderr)
            return 130
