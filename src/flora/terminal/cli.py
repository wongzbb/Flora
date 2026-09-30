# SPDX-License-Identifier: Apache-2.0
"""Default terminal entry point: directory context, guided connection and durable conversations."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flora.general.agent import GeneralAgent, read_profile
from flora.support.errors import FloraError, ValidationError

from .connection import configure
from .sessions import Sessions
from .ui import TerminalUI


def add_launcher_options(parser):
    parser.add_argument(
        "--workspace", "-C", help="Workspace directory (default: current directory)"
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        metavar="ID",
        help="Choose a conversation in this workspace, or resume ID",
    )
    parser.add_argument("--config", help="Optional advanced JSON profile for a new conversation")
    parser.add_argument(
        "--plain", action="store_true", help="Disable animated/colored presentation"
    )
    parser.add_argument(
        "--allow-commands", action="store_true", help="Grant the main agent local command execution"
    )
    parser.add_argument(
        "--no-subagents", action="store_true", help="Disable delegation for a new conversation"
    )


def choose_session(ui, sessions, selected):
    if selected:
        return sessions.select(selected)
    rows = sessions.records()
    if not rows:
        ui.note("No saved conversations in this workspace. Run flora to start one.")
        return None
    ui.sessions(rows)
    while True:
        value = ui.ask("Resume · number or ID · q to cancel").strip()
        if value.lower() in {"q", "/exit"}:
            return None
        if value.isdecimal() and 1 <= int(value) <= len(rows):
            value = rows[int(value) - 1]["id"]
        try:
            return sessions.select(value)
        except ValidationError as exc:
            ui.error(exc)


def run_turn(agent, ui, task=None, *, operation=None):
    """Keep the terminal responsive; Ctrl+C asks the runtime to stop at its next boundary."""
    ui.started = time.monotonic()
    ui.phase = "Working"
    function = operation or (agent.resume if task is None else lambda: agent.run(task))
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flora-main")
    future = pool.submit(function)
    try:
        with ui.live() as live:
            while not future.done():
                try:
                    ui.consume_events()
                    if live:
                        live.update(ui.running())
                    time.sleep(0.08)
                except KeyboardInterrupt:
                    agent.request_pause()
                    ui.phase = "Pause requested · waiting for in-flight operations"
                    ui.note(
                        "Pause requested. Current requests may finish; no action is automatically retried."
                    )
            ui.consume_events()
        return future.result()
    finally:
        pool.shutdown(wait=True)


def show_json(ui, value):
    from rich.text import Text

    from .ui import safe

    ui.console.print(Text(safe(json.dumps(value, ensure_ascii=False, indent=2, default=str))))


def help_text(ui):
    ui.note(
        "Enter sends your task. Alt+Enter inserts a newline. Up/Down recalls input; Tab completes commands."
    )
    ui.note(
        "/help                 Commands and shortcuts\n"
        "/status               Session, task and main-agent budget\n"
        "/agents               Independent subagents and their status\n"
        "/agent ID [OFFSET]    Read a saved subagent result\n"
        "/resume-agent ID      Explicitly continue an interrupted child\n"
        "/resume               Continue this conversation's unfinished task\n"
        "/history              Retained conversation turns\n"
        "/log [CURSOR]         Actual prompts, model output and tool dialogue (paged)\n"
        "/sources              Saved sources (paged)\n"
        "/artifacts            Generated files and current hashes\n"
        "/work                 Durable task goals, evidence and unresolved steps\n"
        "/attach PATH          Attach a workspace file to your next task\n"
        "/new                  Start a fresh conversation in this directory\n"
        "/exit                 Save and exit; Ctrl+D also exits"
    )


def show_budget_mode(ui, agent, status):
    limits = status["budget"]["limits"]
    if all(value is None for value in limits.values()):
        ui.note(
            "Budget: unlimited · usage is recorded · request and runtime protections remain active."
        )
    else:
        ui.note(
            "Budget: configured limits · /status shows caps and cumulative usage; null means unlimited."
        )
        if not agent.unlimited_defaults:
            ui.note(
                "This saved conversation retains its original limits. /new uses unlimited budget defaults."
            )


def conversation(agent, ui, sessions, path, *, initial_task=None):
    ui.note("Session: " + path.name)
    ui.note("Resume here with: flora --resume " + path.name)
    ui.note("/help for commands. Ctrl+C pauses execution; Ctrl+D exits at the prompt.")
    status = agent.status()
    show_budget_mode(ui, agent, status)
    if status["history"]["turns"]:
        ui.history(status["history"], limit=2)
    if agent.delegation:
        rows = agent.delegation.agent_status()["agents"]
        ui.actors = {row["id"]: dict(row) for row in rows}
        if rows:
            ui.agents(rows)
    if status["requires_resume"]:
        ui.note("Unfinished task: " + status["task"].get("task", ""))
        ui.note(
            "Enter /resume to continue it. Inspect /status first if the previous outcome was unknown."
        )
    attachments = []
    while True:
        try:
            task = (
                initial_task
                if initial_task is not None
                else ui.task(history=agent.status()["history"])
            )
            initial_task = None
            task = task.strip()
        except EOFError:
            return "exit"
        except KeyboardInterrupt:
            ui.note("Input cleared. Ctrl+D exits; your conversation is saved.")
            continue
        if not task:
            continue
        command, _, argument = task.partition(" ")
        argument = argument.strip()
        try:
            if task in {"/exit", "/quit"}:
                return "exit"
            if task == "/new":
                return "new"
            if task == "/help":
                help_text(ui)
            elif task == "/status":
                state = agent.status()
                show_budget_mode(ui, agent, state)
                show_json(
                    ui,
                    {
                        key: state[key]
                        for key in (
                            "session",
                            "workspace",
                            "requires_resume",
                            "completed_turns",
                            "budget",
                            "task",
                        )
                    },
                )
                if state.get("last_result"):
                    show_json(
                        ui,
                        {
                            "last_status": state["last_result"].get("status"),
                            "last_reason": state["last_result"].get("reason"),
                        },
                    )
                provider = agent.agent.provider
                show_json(
                    ui,
                    {
                        "transport": {
                            "stream": getattr(provider, "stream", None),
                            "read_timeout_seconds": getattr(provider, "timeout", None),
                            "total_timeout_seconds": getattr(provider, "total_timeout", None),
                            "recovery_calls_per_compilation": agent.agent.compiler.transport_retries,
                        },
                        "actual_dialogue": "/log (latest) or /log 0 (from first retained record)",
                    },
                )
                if agent.delegation:
                    show_json(ui, agent.delegation.agent_status())
            elif task == "/history":
                ui.history(agent.status()["history"])
            elif command == "/log":
                ui.transcript(agent.store.transcript(after=int(argument) if argument else None))
            elif task == "/agents":
                ui.agents(agent.delegation.agent_status()["agents"] if agent.delegation else [])
            elif command in {"/agent", "/resume-agent"}:
                if not agent.delegation:
                    raise ValidationError("Subagents are disabled for this conversation")
                args = argument.split()
                if not 1 <= len(args) <= (2 if command == "/agent" else 1):
                    raise ValidationError("Use /agent ID [OFFSET] or /resume-agent ID")
                if command == "/agent":
                    show_json(
                        ui,
                        agent.delegation.read_agent(
                            args[0], offset=int(args[1]) if len(args) > 1 else 0
                        ),
                    )
                else:
                    agent.delegation.clear_pause()
                    agent.delegation.resume_agent(args[0])

                    def wait_child():
                        while not agent.delegation.futures[args[0]].done():
                            agent.delegation.wait_agents([args[0]], timeout=1)
                        return agent.delegation.read_agent(args[0])

                    show_json(ui, run_turn(agent, ui, operation=wait_child))
            elif task == "/artifacts":
                show_json(ui, agent.artifact_status())
            elif task == "/work":
                show_json(ui, agent.work.read_work() if agent.work else {"available": False, "note": "This saved conversation uses its original protocol"})
            elif command == "/sources":
                show_json(ui, agent.store.list_sources(offset=int(argument) if argument else 0))
            elif command == "/attach":
                target = (sessions.workspace / argument).resolve(strict=True)
                if (
                    not argument
                    or not target.is_file()
                    or not target.is_relative_to(sessions.workspace)
                ):
                    raise ValidationError("Attach a regular file inside the selected workspace")
                if target.stat().st_size > 8388608:
                    raise ValidationError("Attachment exceeds 8 MiB")
                relative = str(target.relative_to(sessions.workspace))
                agent.files._parts(relative)
                if relative not in attachments:
                    attachments.append(relative)
                ui.note("Attached for next task: " + relative)
            elif task == "/resume":
                ui.result(run_turn(agent, ui))
                sessions.touch(path)
            elif task.startswith("/"):
                ui.error("Unknown command. Enter /help for available commands.")
            else:
                if agent.status()["requires_resume"]:
                    ui.error(
                        "An unfinished task exists. Use /resume, or /new for a separate conversation."
                    )
                    continue
                if attachments:
                    task += "\n\nAttached workspace files (read with read_document):\n" + "\n".join(
                        attachments
                    )
                sessions.touch(path, task=task)
                result = run_turn(agent, ui, task)
                attachments.clear()
                ui.result(result)
                if agent.delegation and agent.delegation.records:
                    ui.agents(agent.delegation.agent_status()["agents"])
                sessions.touch(path)
        except (FloraError, OSError, ValueError, TypeError) as exc:
            message = str(exc)
            if agent._session_key:
                message = message.replace(agent._session_key, "[credential redacted]")
            ui.error(message)
            ui.note(
                "Saved execution records remain available. Use /status or /resume; /new starts separately."
            )


def launch(args, *, ui=None):
    ui = ui or TerminalUI(plain=getattr(args, "plain", False))
    connection = None
    try:
        sessions = Sessions(args.workspace or Path.cwd())
        ui.banner(sessions.workspace)
        selected = getattr(args, "resume", None)
        path = choose_session(ui, sessions, selected) if selected is not None else None
        if selected is not None and path is None:
            return 0
        saved = path is not None
        if saved:
            if (
                args.config
                or getattr(args, "allow_commands", False)
                or getattr(args, "no_subagents", False)
            ):
                raise ValidationError(
                    "Resume uses its saved capabilities; omit new-session configuration flags"
                )
            profile = read_profile(path / "general.json", max_bytes=1048576)["profile"]
        else:
            profile = read_profile(args.config) if args.config else {}
            general = profile.setdefault("general", {})
            general.setdefault("subagents", {"enabled": True, "max_parallel": 3, "max_children": 8})
            if getattr(args, "no_subagents", False):
                general["subagents"] = {"enabled": False}
            if getattr(args, "allow_commands", False):
                general["allow_commands"] = True
        connection = configure(ui, profile, saved=saved)
        if path is None:
            path = sessions.create()
        initial = getattr(args, "task", None)
        while True:
            with GeneralAgent(
                session_dir=path,
                workspace=sessions.workspace,
                profile=profile,
                session_key=connection.key,
                on_event=ui.on_event,
            ) as agent:
                action = conversation(agent, ui, sessions, path, initial_task=initial)
                initial = None
                if agent.delegation and agent.delegation.is_busy():
                    ui.note("Pausing active subagents before closing this conversation…")
            if action != "new":
                break
            path = sessions.create()
            ui.input_session = None
            ui.actors.clear()
            ui.note("New conversation created. This process keeps the connection you entered.")
        ui.note("Conversation saved. See you next time, from this directory.")
        return 0
    except (EOFError, KeyboardInterrupt):
        ui.note("Launch cancelled.")
        return 0
    except (FloraError, OSError, ValueError, TypeError) as exc:
        message = str(exc)
        if connection:
            message = message.replace(connection.key, "[credential redacted]")
        ui.error(message)
        return 2
    finally:
        if connection:
            connection.key = ""
