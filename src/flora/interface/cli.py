"""Command-line interface. Dynamic adapters are explicit trusted host code."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from flora import __version__
from flora.checks.contracts import ContractStore
from flora.engine.budget import Budget, BudgetLimits
from flora.engine.replay import replay
from flora.engine.runtime import Runtime, RuntimeConfig
from flora.examples import run_demo
from flora.integrations.adapters import AgentEnvBridge
from flora.integrations.providers import OpenAICompatibleProvider, _strict_json_loads
from flora.integrations.tools import ToolRegistry, ToolSpec
from flora.interface.console import add_direct_commands, direct_main
from flora.language.compiler import LLMCompiler, validate_bundle
from flora.language.ir import parse_program
from flora.state.trace import MemoryTrace, SQLiteTrace
from flora.support.errors import FloraError, ValidationError


def read_json(path: str, *, max_bytes: int = 16_777_216):
    with open(path, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValidationError("Input JSON exceeds configured file byte limit")
    try:
        return _strict_json_loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("Input file is not bounded strict UTF-8 JSON") from exc


def write_json(path: str, value) -> None:
    """Atomic replace in the same directory; never prints credentials."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_adapter(spec: str) -> ToolRegistry | AgentEnvBridge:
    if ":" not in spec:
        raise ValidationError("Adapter must be an explicit module:factory")
    module, name = spec.split(":", 1)
    if not module or not name or "." in name or name.startswith("_"):
        raise ValidationError("Invalid adapter factory name")
    factory = getattr(importlib.import_module(module), name)
    value = factory()
    if isinstance(value, (ToolRegistry, AgentEnvBridge)):
        return value
    if isinstance(value, list) and all(isinstance(t, ToolSpec) for t in value):
        return ToolRegistry(value)
    raise ValidationError(
        "Adapter factory must return ToolRegistry, list[ToolSpec], or AgentEnvBridge"
    )


def load_trace(path: str) -> MemoryTrace:
    """Read-only inspection of SQLite or a trace JSON export."""
    with open(path, "rb") as handle:
        header = handle.read(16)
    if header == b"SQLite format 3\x00":
        db = sqlite3.connect("file:" + quote(str(Path(path).absolute())) + "?mode=ro", uri=True)
        try:
            entries = [
                json.loads(row[0]) for row in db.execute("SELECT entry FROM journal ORDER BY seq")
            ]
            row = db.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
            checkpoint = json.loads(row[0]) if row else None
        finally:
            db.close()
        return MemoryTrace.from_dict(
            {"format": "openharness-trace-v1", "journal": entries, "checkpoint": checkpoint}
        )
    data = read_json(path)
    return MemoryTrace.from_dict(data.get("trace", data))


def _configuration(path):
    if path is None:
        return {}
    config = read_json(path)
    if not isinstance(config, dict) or set(config) - {"runtime", "budget", "provider", "compiler"}:
        raise ValidationError("Config only accepts runtime, budget, provider, compiler sections")
    if any(not isinstance(v, dict) for v in config.values()):
        raise ValidationError("Configuration sections must be JSON objects")
    return config


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flora", description="Run natural-language tasks with Flora"
    )
    p.add_argument("--version", action="version", version=f"Flora {__version__}")
    subs = p.add_subparsers(dest="command")
    add_direct_commands(subs)
    from flora.coding.cli import add_coding_command

    add_coding_command(subs)
    demo = subs.add_parser("demo", help="Execute an offline IR tutorial through the full runtime")
    demo.add_argument("name", choices=["calendar", "pagination"])
    demo.add_argument("--output", help="Write complete result/trace/contracts JSON")
    demo.add_argument("--trace", help="Use a NEW SQLite trace file")
    demo.add_argument("--no-diagnostics", action="store_true")
    demo.add_argument("--no-contracts", action="store_true")
    validate = subs.add_parser(
        "validate", help="Validate a program or bundle without executing tools"
    )
    validate.add_argument("file")
    validate.add_argument("--bundle", action="store_true")
    run = subs.add_parser("run", help="Run authored IR or compile a task with a configured model")
    run.add_argument("--adapter", required=True, help="Explicit trusted Python module:factory")
    run.add_argument("--bundle", help="Authored compiler bundle JSON")
    run.add_argument("--task")
    run.add_argument("--task-file", help="UTF-8 task text, useful for multiline tasks")
    run.add_argument("--config", help="JSON runtime/budget/provider/compiler settings")
    run.add_argument("--model")
    run.add_argument("--base-url", default=None)
    run.add_argument("--api-key-env", default=None)
    run.add_argument("--max-output-tokens", type=int)
    run.add_argument("--trace", help="SQLite journal path; existing checkpoint needs --resume")
    run.add_argument(
        "--resume",
        action="store_true",
        help="Restore the agent; the adapter must reconnect to the SAME external environment",
    )
    run.add_argument("--output")
    run.add_argument("--trace-output")
    inspect_cmd = subs.add_parser("inspect", help="Verify and summarize a recorded trace")
    inspect_cmd.add_argument("trace")
    inspect_cmd.add_argument("--full", action="store_true")
    replay_cmd = subs.add_parser(
        "replay", help="Replay a program against recorded observations without tools"
    )
    replay_cmd.add_argument("program")
    replay_cmd.add_argument("trace")
    replay_cmd.add_argument("--inputs")
    replay_cmd.add_argument("--memory")
    replay_cmd.add_argument("--output")
    contracts = subs.add_parser("contracts", help="Recheck saved contract evidence")
    contracts.add_argument("file", help="ContractStore JSON or demo JSON containing contracts")
    contracts.add_argument("--program", help="Fit a routing guard for this program")
    contracts.add_argument(
        "--relation", choices=["DEFINED_PREFIX", "SAME_BOUNDARY", "SOURCE_FIDELITY"]
    )
    resolve = subs.add_parser(
        "resolve", help="Record an externally established outcome; never reruns a tool"
    )
    resolve.add_argument("trace")
    resolve.add_argument("event_id", type=int)
    resolve.add_argument("--outcome", required=True, help="Known outcome envelope JSON")
    resolve.add_argument("--reason", required=True, help="Where the actual outcome was established")
    subs.add_parser("config", help="Print complete default runtime and budget configuration")
    return p


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command_parser = parser()
    if not arguments:
        if sys.stdin.isatty():
            arguments = ["chat"]
        else:
            command_parser.print_help()
            return 0
    args = command_parser.parse_args(arguments)
    opened_trace = None
    try:
        if args.command == "code":
            from flora.coding.cli import coding_main

            return coding_main(args)
        if args.command in {"ask", "chat", "setup"}:
            return direct_main(args)
        if args.command == "config":
            result = {
                "runtime": asdict(RuntimeConfig()),
                "budget": asdict(BudgetLimits()),
                "provider": {
                    "base_url": "https://api.openai.com/v1",
                    "model": "SET_YOUR_MODEL",
                    "api_key_env": "OPENAI_API_KEY",
                },
                "compiler": {"max_output_tokens": 8192, "max_repairs": 1},
            }
        elif args.command == "demo":
            if args.trace:
                if Path(args.trace).exists():
                    raise ValidationError(
                        "Demo trace must be new; refusing to reuse a different fixture world"
                    )
                opened_trace = SQLiteTrace(args.trace)
            config = RuntimeConfig(
                enable_diagnostics=not args.no_diagnostics, enable_contracts=not args.no_contracts
            )
            data = run_demo(args.name, trace=opened_trace, config=config)
            if args.output:
                write_json(args.output, data)
            result = {
                "execution": data["execution"],
                "result": data["result"],
                "host_observation": data["host_observation"],
            }
        elif args.command == "validate":
            data = read_json(args.file)
            if args.bundle:
                checked = validate_bundle(data)
                result = {"valid": True, "kind": "bundle", "programs": len(checked["programs"])}
            else:
                checked = parse_program(data)
                result = {
                    "valid": True,
                    "kind": "program",
                    "entry": checked["entry"],
                    "blocks": len(checked["blocks"]),
                }
        elif args.command == "run":
            if args.task is not None and args.task_file is not None:
                raise ValidationError("Choose --task or --task-file")
            if args.resume and (
                not args.trace or args.bundle or args.task is not None or args.task_file is not None
            ):
                raise ValidationError(
                    "--resume requires --trace and excludes new --task/--task-file/--bundle"
                )
            task = args.task
            if args.task_file:
                with open(args.task_file, encoding="utf-8") as handle:
                    task = handle.read(262145)
                if len(task) > 262144:
                    raise ValidationError("Task file too large")
            settings = _configuration(args.config)
            adapter = load_adapter(args.adapter)
            completion_guard = None
            if isinstance(adapter, AgentEnvBridge):
                tools = adapter.registry
                completion_guard = adapter.completion_guard
                if task is None and not args.resume:
                    task = adapter.instructions
            else:
                tools = adapter
            budget = Budget(BudgetLimits(**settings.get("budget", {})))
            config = RuntimeConfig(**settings.get("runtime", {}))
            provider_config = settings.get("provider", {}).copy()
            for name, value in (
                ("model", args.model),
                ("base_url", args.base_url),
                ("api_key_env", args.api_key_env),
            ):
                if value is not None:
                    provider_config[name] = value
            compiler = None
            if provider_config.get("model"):
                provider_config.setdefault("base_url", "https://api.openai.com/v1")
                compiler_config = settings.get("compiler", {}).copy()
                if args.max_output_tokens is not None:
                    compiler_config["max_output_tokens"] = args.max_output_tokens
                compiler = LLMCompiler(
                    OpenAICompatibleProvider(**provider_config), **compiler_config
                )
            trace = SQLiteTrace(args.trace) if args.trace else MemoryTrace()
            opened_trace = trace
            if args.resume:
                runtime = Runtime.restore(
                    tools, trace, compiler=compiler, completion_guard=completion_guard
                )
            else:
                if trace.epoch or trace.load_checkpoint() is not None:
                    raise ValidationError("Existing journal requires --resume")
                if not task:
                    raise ValidationError("New runs require --task or --task-file")
                if not args.bundle and compiler is None:
                    raise ValidationError("Provide --bundle or configure a model")
                runtime = Runtime(
                    tools,
                    compiler=compiler,
                    trace=trace,
                    budget=budget,
                    config=config,
                    completion_guard=completion_guard,
                )
            outcome = runtime.run(task, bundle=read_json(args.bundle) if args.bundle else None)
            result = outcome.to_dict()
            if args.output:
                write_json(args.output, result)
            if args.trace_output:
                write_json(args.trace_output, trace.export())
        elif args.command == "inspect":
            trace = load_trace(args.trace)
            result = (
                trace.export()
                if args.full
                else {
                    "valid_hash_chain": True,
                    "epoch": trace.epoch,
                    "digest": trace.digest,
                    "events": [
                        {"event_id": r["event_id"], "tool": r["tool"], "status": r["status"]}
                        for r in trace.records
                    ],
                    "has_checkpoint": trace.load_checkpoint() is not None,
                }
            )
        elif args.command == "replay":
            trace = load_trace(args.trace)
            result = replay(
                read_json(args.program),
                read_json(args.inputs) if args.inputs else {},
                trace.records,
                memory=read_json(args.memory) if args.memory else {},
            )
            if args.output:
                write_json(args.output, result)
        elif args.command == "contracts":
            data = read_json(args.file)
            store = ContractStore.from_dict(data.get("contracts", data))
            result = (
                store.fit_guard(read_json(args.program), relation=args.relation)
                if args.program
                else {
                    "rechecked": True,
                    "records": len(store.records),
                    "dropped_records": store.dropped_records,
                    "verdicts": {
                        v: sum(r["result"]["verdict"] == v for r in store.records)
                        for v in ["PASS", "FAIL", "UNKNOWN"]
                    },
                }
            )
        else:
            if not Path(args.trace).exists():
                raise ValidationError("Resolution requires an existing SQLite trace")
            opened_trace = SQLiteTrace(args.trace)
            result = opened_trace.resolve(
                args.event_id, read_json(args.outcome), reason=args.reason
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        status = result.get("status") if isinstance(result, dict) else None
        if args.command == "demo":
            status = result["result"]["status"]
        return (
            0
            if status in {None, "completed", "returned", "raised"}
            or args.command in {"replay", "resolve", "contracts"}
            else 2
        )
    except (
        FloraError,
        OSError,
        ValueError,
        TypeError,
        ImportError,
        AttributeError,
        sqlite3.Error,
    ) as exc:
        if args.command in {"ask", "chat", "setup", "code"} and not getattr(args, "json", False):
            print(f"Flora: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    finally:
        if opened_trace is not None:
            opened_trace.close()


if __name__ == "__main__":
    raise SystemExit(main())
