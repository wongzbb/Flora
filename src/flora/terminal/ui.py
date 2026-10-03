# SPDX-License-Identifier: Apache-2.0
"""Terminal presentation: overview pixel art, accessible prompts and real runtime events."""

from __future__ import annotations

import json
import os
import queue
import sys
import time
import unicodedata
from collections import deque
from contextlib import nullcontext
from importlib.resources import files

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

COLORS = {
    "navy": "#0b101b",
    "panel": "#101827",
    "muted": "#a1aec3",
    "line": "#283347",
    "white": "#f0f1ee",
    "mint": "#98f5c6",
    "violet": "#c2b1ff",
    "pink": "#efa7cb",
}
COMMANDS = [
    "/help",
    "/status",
    "/agents",
    "/agent",
    "/resume-agent",
    "/resume",
    "/history",
    "/log",
    "/sources",
    "/artifacts",
    "/work",
    "/details",
    "/attach",
    "/new",
    "/exit",
]


def safe(value):
    """Remove terminal escape/control and bidi-format characters from untrusted display text."""
    return "".join(
        c for c in str(value) if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
    )


def pixel_art(name):
    pixels = json.loads(files("flora.terminal").joinpath("assets/pixels.json").read_text())
    rows = pixels[name]
    text = Text()
    for y in range(0, len(rows), 2):
        for x in range(len(rows[y])):
            top = rows[y][x] or COLORS["panel"]
            bottom = rows[min(y + 1, len(rows) - 1)][x] or COLORS["panel"]
            text.append("▀", style=f"{top} on {bottom}")
        if y + 2 < len(rows):
            text.append("\n")
    return text


class TerminalUI:
    def __init__(self, *, plain=False, console=None):
        self.console = console or Console(
            theme=Theme(COLORS), highlight=False, no_color=plain or "NO_COLOR" in os.environ
        )
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty()
        self.plain = (
            plain
            or not self.interactive
            or "NO_COLOR" in os.environ
            or os.environ.get("TERM") == "dumb"
        )
        self.events = queue.Queue(maxsize=2048)
        self.recent = deque(maxlen=4)
        self.actors = {}
        self.phase = "Ready"
        self.started = time.monotonic()
        self.input_session = None
        self.dialogue = {}
        self.dialogue_truncated = set()
        self.dropped_events = 0
        # Keep the existing Rich visual language while making the composer a
        # stable, stateful prompt-toolkit surface. Verbose execution panels are
        # collapsed by default; /details expands them without affecting /log.
        self.show_execution_details = False
        self.session_name = ""
        self.workspace = ""
        self.model = ""
        self.last_result_status = "Ready"

    def set_context(self, *, session=None, workspace=None, model=None):
        """Set session/workspace/model labels shown in the composer toolbar."""
        if session is not None:
            self.session_name = safe(session)
        if workspace is not None:
            self.workspace = safe(workspace)
        if model is not None:
            self.model = safe(model)

    def banner(self, workspace):
        if self.plain or self.console.width < 64:
            self.console.print(Text("✿ FLORA  ·  Terminal Agent", style="violet"))
            self.console.print(Text("Act to get things done—and to find things out.", style="mint"))
            self.note("Workspace: " + str(workspace))
            return
        brand = Table.grid(padding=(0, 2))
        brand.add_column(width=10)
        brand.add_column()
        brand.add_row(pixel_art("logo"), Text("FLORA\nTERMINAL AGENT\n0.1.0", style="bold violet"))
        copy = Group(
            brand,
            Text(""),
            Text("Act to get things done—\nand to find things out.", style="mint"),
            Text(""),
            Text("Workspace", style="muted"),
            Text(safe(workspace), style="white"),
            Text(""),
            Text("/help  commands     /agents  team", style="muted"),
        )
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(width=30)
        table.add_column(ratio=1)
        table.add_row(pixel_art("mascot"), copy)
        self.console.print(
            Panel(
                table,
                border_style="violet",
                style="on #101827",
                padding=(1, 2),
                title="[mint]PIXEL BLOOM / FLORA[/mint]",
            )
        )

    def ask(self, label, *, secret=False):
        if not self.interactive or self.plain:
            if secret:
                import getpass

                if not sys.stdin.isatty():
                    # Explicit piped mode is useful for scripted launchers; no prompt echo.
                    self.console.print(Text(label + " › ", style="pink"), end="")
                    line = sys.stdin.readline()
                    if not line:
                        raise EOFError
                    return line.rstrip("\r\n")
                return getpass.getpass(label + " › ")
            return input(label + " › ")
        return PromptSession(history=InMemoryHistory(), erase_when_done=secret).prompt(
            [("fg:" + COLORS["pink" if secret else "mint"], label + " › ")],
            is_password=secret,
        )

    def task(self, *, history=None):
        if self.plain:
            return input("flora › ")
        if self.input_session is None:
            keys = KeyBindings()

            @keys.add("escape", "enter")
            def newline(event):
                event.current_buffer.insert_text("\n")

            @keys.add("enter")
            def submit(event):
                event.current_buffer.validate_and_handle()

            entries = [r["task"] for r in (history or {}).get("turns", [])]
            self.input_session = PromptSession(
                history=InMemoryHistory(entries),
                completer=WordCompleter(COMMANDS, sentence=True),
                complete_while_typing=False,
                key_bindings=keys,
                multiline=True,
                style=Style.from_dict(
                    {
                        "prompt": COLORS["mint"],
                        "composer-border": COLORS["violet"],
                        "toolbar-label": COLORS["mint"],
                        "toolbar-muted": COLORS["muted"],
                        "bottom-toolbar": "bg:#101827 #a1aec3",
                        "completion-menu.completion.current": "bg:#c2b1ff #0b101b",
                    }
                ),
                bottom_toolbar=self.composer_toolbar,
            )
        return self.input_session.prompt(
            [("class:composer-border", "╭─ "), ("class:prompt", "flora › ")],
            prompt_continuation=[("class:composer-border", "│ ")],
        )

    def composer_toolbar(self):
        """Return the compact state line rendered below the fixed composer."""
        active = sum(
            1 for row in self.actors.values() if row.get("status") in {"queued", "running"}
        )
        total = len(self.actors)
        detail = "shown" if self.show_execution_details else "collapsed"
        parts = [
            ("class:composer-border", "╰─ "),
            ("class:toolbar-label", " FLORA "),
            ("class:toolbar-muted", f"{self.phase} · {self.last_result_status}"),
        ]
        if total:
            parts.append(("class:toolbar-muted", f" · agents {active}/{total}"))
        if self.model:
            parts.append(("class:toolbar-muted", f" · model {self.model}"))
        if self.workspace:
            parts.append(("class:toolbar-muted", f" · {self.workspace}"))
        parts.extend(
            [
                ("class:toolbar-muted", f" · details {detail}"),
                (
                    "class:toolbar-muted",
                    " · Enter send · Alt+Enter newline · /details · Ctrl+C pause",
                ),
            ]
        )
        return parts

    def note(self, value):
        self.console.print(Text(safe(value), style="muted"))

    def error(self, value):
        self.console.print(Text(safe(value), style="pink"))

    def waiting(self, label):
        return self.console.status(Text(label, style="mint")) if not self.plain else nullcontext()

    def models(self, models):
        if not models:
            return
        table = Table(title="Available models", border_style="line", show_lines=False)
        table.add_column("#", style="mint", justify="right")
        table.add_column("Model ID", style="white", overflow="fold")
        for i, model in enumerate(models, 1):
            table.add_row(str(i), Text(safe(model)))
        self.console.print(table)
        self.note(f"{len(models)} model IDs returned. You may also enter an unlisted model ID.")

    def sessions(self, records):
        table = Table(title="Conversations in this workspace", border_style="violet", expand=True)
        for name in ("#", "Session ID", "Conversation", "Turns", "Status"):
            table.add_column(name, overflow="fold")
        for i, row in enumerate(records, 1):
            table.add_row(
                str(i), row["id"], Text(safe(row["title"])), str(row["turns"]), row["status"]
            )
        self.console.print(table)

    def on_event(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # This is only a view queue. Durable events remain in the observation store.
            self.dropped_events += 1

    def consume_events(self):
        if self.dropped_events:
            self.note(
                f"{self.dropped_events} display events omitted; /log reads the retained transcript."
            )
            self.dropped_events = 0
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get("kind", "")
            if kind == "transcript":
                self.consume_dialogue(event)
                continue
            if kind in {"subagent_status", "subagent_spawned", "subagent_event"}:
                ident = event["agent_id"]
                actor = self.actors.setdefault(
                    ident,
                    {"id": ident, "name": event.get("name", ""), "status": "queued", "detail": ""},
                )
                if kind == "subagent_event":
                    actor["detail"] = self.describe(event["event"])
                    actor["status"] = "running"
                else:
                    # A fast worker may finish before spawn_agent returns its ID.
                    if kind != "subagent_spawned" or actor["status"] == "queued":
                        actor.update(
                            {k: event[k] for k in ("name", "status", "detail") if k in event}
                        )
            else:
                message = self.describe(event)
                if message:
                    self.phase = message
                    if not self.recent or message != self.recent[-1]:
                        self.recent.append(message)
                        if self.plain:
                            self.note("Flora · " + message)

    @staticmethod
    def describe(event):
        kind = event.get("kind")
        if kind == "model_call_started":
            return "Requesting model · call " + str(event.get("model_calls", ""))
        if kind == "action_selected":
            label = "Investigating" if event.get("diagnostic") else "Using"
            return label + " · " + str(event.get("tool", "tool"))
        if kind == "tool_result":
            return str(event.get("tool", "tool")) + " · " + str(event.get("status", "returned"))
        return {
            "bundle_installed": "Program ready",
            "replan_requested": "Revising from observations",
            "revision_checked": "Checking local contracts",
            "checkpoint_restored": "Restored checkpoint",
            "candidate_suspended": "Candidate suspended",
            "task_finished": "Task finished",
        }.get(kind, "")

    def consume_dialogue(self, event):
        actor, channel = event["actor"], event["channel"]
        text = safe(event["text"])
        label = "Flora" if actor == "main" else actor
        if channel.startswith("prompt/"):
            return  # Full actual outbound prompts are available through /log.
        if channel in {"program", "reasoning"}:
            key = (actor, event.get("request", ""), channel)
            old = self.dialogue.get(key, "")
            if len(old + text) > 12000:
                self.dialogue_truncated.add(key)
            self.dialogue[key] = (old + text)[-12000:]
            if self.plain:
                self.console.print(Text(f"{label} · {channel} › {text}", style="white"))
            self.phase = label + " · receiving " + channel
            return
        if channel in {
            "model_response",
            "model_failure",
            "compiler_rejected",
            "compiler_validated",
        }:
            for key in list(self.dialogue):
                if key[0] == actor:
                    if not self.plain:
                        title = (
                            "model output · pending validation"
                            if key[2] == "program"
                            else "provider reasoning_content"
                        )
                        if key in self.dialogue_truncated:
                            title += " · last 12,000 characters; /log for more"
                        if self.show_execution_details:
                            self.console.print(
                                Panel(
                                    Text(self.dialogue[key]),
                                    title=safe(label + " / " + title),
                                    border_style="violet" if key[2] == "program" else "line",
                                )
                            )
                        else:
                            self.note(label + " · " + title + " · collapsed; use /details or /log")
                    del self.dialogue[key]
                    self.dialogue_truncated.discard(key)
        if channel == "request":
            self.note(label + " · model request " + text + " · outbound prompts: /log 0")
        elif channel.startswith("tool/"):
            tool = safe(event.get("tool", ""))
            if self.show_execution_details:
                self.console.print(
                    Panel(
                        Text(text[:6000] + ("\n… /log for more" if len(text) > 6000 else "")),
                        title=safe(f"{label} / {channel} / {tool}"),
                        border_style="mint",
                    )
                )
            else:
                self.note(label + " · " + tool + " · result collapsed; use /details or /log")
        else:
            self.note(label + " · " + channel + " · " + text)

    def transcript(self, page):
        if page["history_truncated"]:
            self.note("Older transcript records are outside the rolling 8 MiB display log.")
        if not page["records"]:
            self.note(
                "No transcript records at this cursor. Earlier releases did not record dialogue."
            )
        for row in page["records"]:
            self.console.print(
                Panel(
                    Text(safe(row["text"])),
                    title=safe(f"#{row['id']} / {row['actor']} / {row['channel']}"),
                    border_style="line",
                )
            )
        self.note(
            f"Next page: /log {page['next_cursor']}"
            + ("" if page["has_more"] else " · end of retained log")
        )

    def running(self):
        elapsed = int(time.monotonic() - self.started)
        head = Table.grid(expand=True)
        head.add_column(ratio=1)
        head.add_column(justify="right")
        head.add_row(
            Spinner("dots", text=Text(safe(self.phase), style="mint"), style="violet"),
            Text(f"{elapsed}s", style="muted"),
        )
        elements = [head]
        for (actor, _, channel), text in list(self.dialogue.items())[-4:]:
            tail = "\n".join(text[-1600:].splitlines()[-8:])
            elements.append(
                Panel(
                    Text(safe(tail)),
                    title=safe(actor + " / " + channel + " · live"),
                    border_style="violet",
                )
            )
        if self.actors:
            elements.append(self.agent_table(list(self.actors.values())))
        elements.append(
            Text(
                "Ctrl+C pauses before the next action; an in-flight request may finish.",
                style="muted",
            )
        )
        return Panel(
            Group(*elements), title="[violet]FLORA / EXECUTION[/violet]", border_style="line"
        )

    def live(self):
        return (
            Live(self.running(), console=self.console, refresh_per_second=8, transient=True)
            if not self.plain
            else nullcontext(None)
        )

    def agent_table(self, records):
        table = Table(border_style="line", expand=True)
        for header in ("Subagent", "Task / activity", "Status"):
            table.add_column(header, overflow="fold")
        for row in records:
            title = row.get("task") or row.get("detail", "")
            if row.get("task") and row.get("detail"):
                title = row["task"][:160] + "\n" + row["detail"]
            table.add_row(
                Text(safe(row.get("name", "") + "\n" + row["id"]), style="violet"),
                Text(safe(title)),
                Text(safe(row["status"]), style="mint"),
            )
        return table

    def agents(self, records):
        if records:
            self.console.print(self.agent_table(records))
        else:
            self.note("No subagents have been created in this conversation.")

    def result(self, result):
        self.last_result_status = safe(result.get("status", "unknown"))
        value = result.get("value")
        body = []
        if value is not None:
            if isinstance(value, str):
                body.append(Markdown(safe(value)) if not self.plain else Text(safe(value)))
            else:
                body.append(Text(safe(json.dumps(value, ensure_ascii=False, indent=2))))
        body.append(Text("Status: " + self.last_result_status, style="mint"))
        if result.get("reason"):
            body.append(Text(safe(result["reason"]), style="muted"))
        if result.get("failure"):
            body.append(
                Text(
                    safe(result["failure"]["code"] + " · " + result["failure"]["next_action"]),
                    style="pink",
                )
            )
        if self.plain:
            self.console.print(Text("\nFlora", style="bold violet"))
            for item in body:
                self.console.print(item)
        else:
            self.console.print(
                Panel(
                    Group(*body),
                    title="[violet]FLORA / RESULT[/violet]",
                    border_style="violet" if self.last_result_status == "completed" else "pink",
                )
            )
        checks = result.get("completion_checks", {})
        for section in ("work", "children"):
            for item in checks.get(section, {}).get("limitations", []):
                self.note("Unresolved: " + item["note"])
        for item in result.get("artifacts", []):
            if item.get("current_task"):
                self.note("Artifact: " + item["path"])
        if result["status"] not in {"completed", "paused"}:
            self.note(
                "Saved as unfinished. /status shows details; /resume continues the same task and budget."
            )

    def history(self, history, *, limit=None):
        turns = history.get("turns", [])
        for row in turns[-limit:] if limit else turns:
            self.console.print(Text("You · " + safe(row["task"]), style="mint"))
            self.result({"status": row["status"], "value": row.get("value")})
        if history.get("omitted_turns"):
            self.note(
                f"{history['omitted_turns']} older turns are outside the retained model context."
            )
