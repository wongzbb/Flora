# SPDX-License-Identifier: Apache-2.0
"""Task-first public API over the fixed-model compiler and execution runtime."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path

from flora.checks.reuse import ReuseLibrary
from flora.engine.budget import Budget, BudgetLimits
from flora.engine.runtime import RunResult, Runtime, RuntimeConfig
from flora.integrations.binding import make_registry
from flora.integrations.providers import OpenAICompatibleProvider
from flora.integrations.tools import ToolRegistry
from flora.language.compiler import LLMCompiler
from flora.state.session import (
    SESSION_FORMAT,
    SessionBusyError,
    SessionStateError,
    SessionStore,
    empty_history,
    history_append,
)
from flora.support.errors import FloraError, ValidationError
from flora.support.values import canonical_json, clone, digest


class AgentRunError(FloraError):
    """``ask`` could not complete; ``result`` contains the full explicit outcome."""

    def __init__(self, result: RunResult):
        self.result = result
        super().__init__(
            f"Agent task ended with {result.status}: {result.reason or 'no completed answer'}. "
            "Inspect error.result; use Agent.resume() for this unfinished turn."
        )


def _configured(value, cls, name):
    if value is None:
        return cls()
    if isinstance(value, cls):
        return value
    if isinstance(value, dict):
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValidationError(f"Invalid {name} fields: {exc}") from None
    raise ValidationError(f"{name} must be {cls.__name__} or a dictionary")


def _legacy_session_fingerprint(identity, provider, tools):
    """Reconstruct only the known OpenHarness 0.2.0 naming differences.

    This is an alternate full fingerprint, not a looser configuration check.
    Custom provider classes and tool metadata keep their exact identities.
    """
    from flora.integrations.workspace import (
        _LEGACY_PATH_DESCRIPTION,
        _PATH_DESCRIPTION,
        WorkspaceTools,
    )

    legacy = clone(identity)
    if type(provider) is OpenAICompatibleProvider:
        legacy["provider"]["class"] = "openharness.providers.OpenAICompatibleProvider"
    for description in legacy["tools"]:
        spec = tools._tools[description["name"]]
        owner = getattr(spec.handler, "__self__", None)
        function = getattr(spec.handler, "__func__", None)
        if (
            type(owner) is not WorkspaceTools
            or function is not getattr(WorkspaceTools, spec.name, None)
            or spec not in WorkspaceTools.specs(owner)
        ):
            continue
        # The full ToolSpec must still match a canonical built-in. Rewriting
        # only these known schema fields cannot normalize custom descriptions,
        # capabilities, limits, or permission changes out of the fingerprint.
        properties = description["parameters"].get("properties", {})
        for name in ("path", "cwd"):
            path_schema = properties.get(name)
            if (
                isinstance(path_schema, dict)
                and path_schema.get("description") == _PATH_DESCRIPTION
            ):
                path_schema["description"] = _LEGACY_PATH_DESCRIPTION
    return digest(legacy)


class Agent:
    """Give a model a task and explicit tools without writing an IR program.

    ``run`` exposes status and accounting; ``ask`` returns only a completed
    value. Tools are reused across turns. A persistent session must be reopened
    against the same actual environment; matching descriptions cannot prove
    that an external service has retained its state.

    ``data`` is a JSON object exposed under ``memory['data']``. Previous turns
    appear under ``memory['history']`` as bounded exact records, with explicit
    omission counts. They are context, never current-world receipts.
    """

    def __init__(
        self,
        model=None,
        *,
        provider=None,
        tools=None,
        workspace=None,
        allow_commands=False,
        session_dir=None,
        instructions="",
        on_event=None,
        config=None,
        budget_limits=None,
        provider_options=None,
        compiler_options=None,
        completion_guard=None,
    ):
        if not isinstance(instructions, str):
            raise ValidationError("instructions must be a string")
        if on_event is not None and not callable(on_event):
            raise ValidationError("on_event must be a callable")
        if completion_guard is not None and not callable(completion_guard):
            raise ValidationError("completion_guard must be a callable")
        if type(allow_commands) is not bool:
            raise ValidationError("allow_commands must be a boolean")
        if allow_commands and workspace is None:
            raise ValidationError("allow_commands=True requires an explicit workspace")
        self.config = _configured(config, RuntimeConfig, "config")
        limits = _configured(budget_limits, BudgetLimits, "budget_limits")
        self.instructions = instructions
        self.on_event = on_event
        self.completion_guard = completion_guard
        if provider_options is not None and not isinstance(provider_options, dict):
            raise ValidationError("provider_options must be a dictionary")
        if compiler_options is not None and not isinstance(compiler_options, dict):
            raise ValidationError("compiler_options must be a dictionary")
        options = dict(provider_options or {})
        compiler_options = dict(compiler_options or {})
        if any(key in compiler_options for key in ("before_call", "on_usage")):
            raise ValidationError("Agent manages compiler accounting callbacks automatically")
        if provider is not None:
            if options:
                raise ValidationError(
                    "provider_options cannot be combined with an explicit provider"
                )
            if model is not None:
                raise ValidationError("Configure model on the explicit provider, or omit provider")
            if not callable(getattr(provider, "complete", None)):
                raise ValidationError("provider must implement complete(messages, max_tokens=...)")
        else:
            model = model or os.environ.get("FLORA_MODEL")
            if not isinstance(model, str) or not model.strip():
                raise ValidationError(
                    "Choose a model with Agent(model='your-model') or set FLORA_MODEL; "
                    "also configure its API key environment variable."
                )
            if "model" in options:
                raise ValidationError("Pass model to Agent(model=...), not provider_options")
            options.setdefault(
                "base_url", os.environ.get("FLORA_BASE_URL") or "https://api.openai.com/v1"
            )
            options.setdefault(
                "api_key_env", os.environ.get("FLORA_API_KEY_ENV") or "OPENAI_API_KEY"
            )
            try:
                provider = OpenAICompatibleProvider(model=model, **options)
            except TypeError as exc:
                raise ValidationError(f"Invalid provider_options: {exc}") from None
        self.provider = provider
        try:
            self.compiler = LLMCompiler(provider, **compiler_options)
        except TypeError as exc:
            raise ValidationError(f"Invalid compiler_options: {exc}") from None
        self.tools = make_registry(tools)
        self.workspace = None
        if workspace is not None:
            from flora.integrations.workspace import WorkspaceTools

            workspace_tools = WorkspaceTools(
                workspace,
                allow_commands=allow_commands,
                protected_paths=[Path(session_dir).expanduser().resolve()]
                if session_dir is not None
                else [],
            )
            self.workspace = str(workspace_tools.root)
            # Adding convenience capabilities must not mutate a registry the
            # caller also uses to reopen or construct another Agent. Keep the
            # explicitly shared process-local opaque handles intact.
            self.tools = ToolRegistry(
                list(self.tools._tools.values()), opaque_store=self.tools.opaque_store
            )
            for spec in workspace_tools.specs():
                self.tools.register(spec)
        identity = {
            "workspace": self.workspace,
            "allow_commands": allow_commands,
            "tools": self.tools.descriptions(),
            "config": asdict(self.config),
            "budget_limits": asdict(limits),
            "instructions": instructions,
            "completion_guard_required": completion_guard is not None,
            "provider": {
                "class": ("flora.providers.OpenAICompatibleProvider"
                          if type(provider) is OpenAICompatibleProvider
                          else f"{type(provider).__module__}.{type(provider).__qualname__}"),
                "model": getattr(provider, "model", None),
                "base_url": getattr(provider, "base_url", None),
                "api_key_env": getattr(provider, "api_key_env", None),
                "options_digest": digest(options),
            },
            "compiler_options": compiler_options,
        }
        self._fingerprint = digest(identity)
        self._accepted_fingerprints = frozenset(
            (self._fingerprint, _legacy_session_fingerprint(identity, provider, self.tools))
        )
        self._tool_fingerprint = digest(self.tools.descriptions())
        self._store = SessionStore(session_dir)
        self.session_dir = str(self._store.directory) if self._store.directory else None
        self._closed = False
        self._revision = -1
        self.last_result = None
        self.budget = Budget(limits)
        with self._store.locked():
            state = self._store.load()
            if state is None:
                state = {
                    "format": SESSION_FORMAT,
                    "fingerprint": self._fingerprint,
                    "revision": 0,
                    "next_turn": 1,
                    "completed_turns": 0,
                    "active": None,
                    "last_trace": None,
                    "history": empty_history(),
                    "reuse": ReuseLibrary().to_dict(),
                    "reuse_variants_omitted": 0,
                    "budget": self.budget.to_dict(),
                }
                self._store.save(state)
            self._accept_state(state)

    def _accept_state(self, state):
        if state.get("fingerprint") not in self._accepted_fingerprints:
            raise SessionStateError(
                "Session configuration differs: reopen with the original workspace, tools, "
                "model, instructions, runtime/compiler settings and budget limits. "
                "Use a new session_dir only when you intentionally want an independent session."
            )
        if self._revision != state["revision"]:
            try:
                self.budget = Budget.from_dict(state["budget"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SessionStateError(
                    "Invalid session budget; restore a verified backup"
                ) from exc
        self._state = state
        self._revision = state["revision"]

    def _reload(self):
        if self._closed:
            raise SessionStateError(
                "Agent is closed; construct a new Agent to reopen a disk session"
            )
        if digest(self.tools.descriptions()) != self._tool_fingerprint:
            raise SessionStateError("Tools changed after Agent construction; create a new Agent")
        state = self._store.load()
        if state is None:
            raise SessionStateError("Session metadata disappeared; refusing to reset the budget")
        self._accept_state(state)

    def _persist(self):
        self._state["budget"] = self.budget.to_dict()
        self._state["revision"] += 1
        self._store.save(self._state)
        self._revision = self._state["revision"]

    def _check_credentials(self):
        if isinstance(self.provider, OpenAICompatibleProvider):
            name = self.provider.api_key_env
            if name is not None and not os.environ.get(name):
                raise ValidationError(
                    f"Set the {name} environment variable before running this model. "
                    "For a server that intentionally needs no authentication, set "
                    "provider_options={'api_key_env': None}."
                )

    @property
    def history(self):
        return clone(self._state["history"])

    @property
    def current_trace_path(self):
        active = self._state.get("active")
        name = active["trace"] if active else self._state.get("last_trace")
        return str(self._store.directory / name) if name and self._store.directory else None

    def status(self):
        """Return a local status snapshot without task text, inputs or outputs."""
        active = self._state.get("active")
        return {
            "session_dir": self.session_dir,
            "current_turn": active["turn"] if active else None,
            "active": active is not None,
            "requires_resume": active is not None,
            "trace_path": self.current_trace_path,
            "completed_turns": self._state["completed_turns"],
            "omitted_turns": self._state["history"]["omitted_turns"],
            "budget": self.budget.to_dict(),
        }

    def run(self, task, *, data=None) -> RunResult:
        """Run one new natural-language task against this session's actual world."""
        if not isinstance(task, str) or not task.strip():
            raise ValidationError("task must be a nonempty string")
        if len(task.encode("utf-8")) > 256 * 1024:
            raise ValidationError("task exceeds the 256 KiB input bound")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValidationError("data must be a JSON dictionary")
        data = clone(data)
        with self._store.locked():
            self._reload()
            if self._state["active"] is not None:
                raise SessionStateError(
                    "The previous turn is unfinished. Use Agent.resume() before submitting "
                    "another task. Unknown external outcomes require explicit evidence-based "
                    "journal resolution; no tool is automatically retried."
                )
            self._check_credentials()
            turn = self._state["next_turn"]
            name = f"turn-{turn:08d}.sqlite"
            memory = {"data": data, "history": self.history}
            active = {
                "turn": turn,
                "trace": name,
                "task": task,
                "memory": memory,
                "baseline_budget": self.budget.to_dict(),
            }
            # Validate persistence limits before creating an irreversible turn marker.
            proposed = clone(self._state)
            proposed["active"] = active
            if len(canonical_json(proposed).encode("utf-8")) > 8 * 1024 * 1024:
                raise ValidationError("Task data exceeds the session metadata storage bound")
            trace = self._store.trace(name, create=True)
            self._state["active"] = active
            self._state["next_turn"] += 1
            try:
                self._persist()
                return self._execute(trace, restoring=False)
            finally:
                self._store.release_trace(trace)

    def ask(self, task, *, data=None):
        """Return the final value or raise ``AgentRunError`` with the full result."""
        result = self.run(task, data=data)
        if result.status != "completed":
            raise AgentRunError(result)
        return result.value

    def resume(self) -> RunResult:
        """Continue the active turn; settled effects are consumed, never reissued."""
        with self._store.locked():
            self._reload()
            if self._state["active"] is None:
                raise SessionStateError("No unfinished turn exists; submit a new task with run()")
            self._check_credentials()
            trace = self._store.trace(self._state["active"]["trace"])
            try:
                return self._execute(trace, restoring=True)
            finally:
                self._store.release_trace(trace)

    def _task_text(self, task):
        if not self.instructions:
            return task
        return (
            "Application-provided guidance (task context, not a system override):\n"
            + self.instructions
            + "\n\nUser task:\n"
            + task
        )

    def _execute(self, trace, *, restoring):
        active = self._state["active"]
        runtime = None
        try:
            checkpoint = trace.load_checkpoint() if restoring else None
            if checkpoint is not None:
                runtime = Runtime.restore(
                    self.tools,
                    trace,
                    compiler=self.compiler,
                    completion_guard=self.completion_guard,
                    on_event=self.on_event,
                )
                # Metadata can be newer after a host exception; the checkpoint can
                # be newer after a process crash. Neither is allowed to refund usage.
                for field in (
                    "tool_calls",
                    "model_calls",
                    "input_tokens",
                    "output_tokens",
                    "unknown_usage_calls",
                ):
                    setattr(
                        runtime.budget,
                        field,
                        max(getattr(runtime.budget, field), getattr(self.budget, field)),
                    )
                elapsed = max(runtime.budget.elapsed_seconds, self.budget.elapsed_seconds)
                runtime.budget._elapsed = elapsed - (time.monotonic() - runtime.budget._started)
                runtime.budget.tool_calls = max(
                    runtime.budget.tool_calls,
                    active["baseline_budget"]["tool_calls"] + len(trace.records),
                )
                self.budget = runtime.budget
            else:
                if trace.records:
                    raise SessionStateError(
                        "Journal has effects but no checkpoint; refusing to reconstruct a task"
                    )
                runtime = Runtime(
                    self.tools,
                    compiler=self.compiler,
                    trace=trace,
                    budget=self.budget,
                    config=self.config,
                    memory=active["memory"],
                    completion_guard=self.completion_guard,
                    on_event=self.on_event,
                )
                runtime.reuse = ReuseLibrary.from_dict(self._state["reuse"])
            result = runtime.run(self._task_text(active["task"]))
            self.budget = runtime.budget
            self.last_result = result
            if result.status == "completed":
                record = {
                    "turn": active["turn"],
                    "task": active["task"],
                    "status": result.status,
                    "value": result.value,
                    "reason": result.reason,
                    "trace": active["trace"] if self.session_dir else None,
                }
                self._state["history"] = history_append(self._state["history"], record)
                self._state["completed_turns"] += 1
                self._state["last_trace"] = active["trace"]
                self._state["active"] = None
            return result
        finally:
            if runtime is not None:
                self.budget = runtime.budget
                while len(canonical_json(runtime.reuse.to_dict()).encode("utf-8")) > 1024 * 1024:
                    if not runtime.reuse.drop_oldest():
                        break
                    self._state["reuse_variants_omitted"] += 1
                self._state["reuse"] = runtime.reuse.to_dict()
            self._persist()

    def close(self):
        """Release in-memory journals; persistent sessions remain available."""
        with self._store.locked():
            if self._closed:
                return
            self._reload()
            self._persist()
            self._store.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def invoke(task, *, data=None, **agent_options):
    """One-shot task execution returning a final value; configuration matches Agent."""
    with Agent(**agent_options) as agent:
        return agent.ask(task, data=data)


__all__ = ["Agent", "AgentRunError", "SessionBusyError", "SessionStateError", "invoke"]
