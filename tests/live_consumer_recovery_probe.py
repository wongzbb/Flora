"""Opt-in actual-API repair AFTER an intentionally faulted consumer; not E2E tasks.

Seeded programs execute real file effects, then fail a pure field access. Only the
subsequent repair is model-generated. No application code injects these faults.
Run from the repository: PYTHONPATH=src python -m tests.live_consumer_recovery_probe ...
"""

from __future__ import annotations

import argparse
import getpass
import json
import signal
import time
from pathlib import Path

from flora.engine.budget import Budget, BudgetLimits
from flora.engine.runtime import Runtime, RuntimeConfig
from flora.general.documents import DocumentWorkspace
from flora.general.observability import Dialogue
from flora.general.schemas import bounded_specs
from flora.general.storage import ObservationStore
from flora.integrations.binding import make_registry
from flora.integrations.providers import OpenAICompatibleProvider
from flora.integrations.tools import ToolRegistry
from flora.language.compiler import LLMCompiler
from tests.test_consumer_recovery import consumer_fault_program


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    key = getpass.getpass("API key (hidden; memory only): ")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    profile = json.loads(args.profile.read_text())
    results = []

    def save(path, value):
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2).replace(key, "[REDACTED]") + "\n"
        )

    try:
        for n in range(args.rounds):
            for name in ("read", "publish"):
                case = output / f"r{n + 1}-{name}"
                workspace = case / "workspace"
                workspace.mkdir(parents=True)
                original = json.dumps({"project": f"Observed project {n + 1}", "amount": 7 - n})
                (workspace / "input.json").write_text(original)
                files = DocumentWorkspace(workspace)
                (case / "dialogue").mkdir()
                store = ObservationStore(case / "dialogue")
                dialogue = Dialogue(store, None, secrets=(key,))
                registry = make_registry([files.read_file, files.create_file])
                specs = bounded_specs(list(registry._tools.values()), describe_results=True)
                tools = ToolRegistry(dialogue.tools(specs))
                options = {
                    **profile["provider"],
                    "base_url": args.base_url,
                    "model": args.model,
                    "api_key_env": None,
                    "allow_insecure_http": True,
                    "stream": True,
                }
                provider = OpenAICompatibleProvider(**options)
                provider.set_session_key(key)
                provider.on_event = dialogue.event
                compiler = LLMCompiler(provider, **profile["compiler"])
                compiler.transport_retries = 1
                compiler.on_event = dialogue.event
                runtime = Runtime(
                    tools,
                    compiler=compiler,
                    config=RuntimeConfig(max_compile_cycles=3),
                    budget=Budget(
                        BudgetLimits(
                            max_tool_calls=4,
                            max_model_calls=4,
                            max_input_tokens=200000,
                            max_output_tokens=48000,
                            max_wall_seconds=180,
                        )
                    ),
                )
                if name == "read":
                    task = "读取 input.json，只返回其中 project 字段的值，不修改任何文件。"
                    tool, arguments = "read_file", {"path": "input.json"}
                    expected = f"Observed project {n + 1}"
                else:
                    task = f"新建 published.txt，内容为 public note {n + 1}，不要覆盖已有文件。完成后只返回实际文件的相对路径。"
                    tool, arguments = (
                        "create_file",
                        {"path": "published.txt", "content": f"public note {n + 1}"},
                    )
                    expected = "published.txt"
                seed = consumer_fault_program(tool, arguments)
                save(case / "seeded-program.json", seed)
                row = {
                    "case": name,
                    "round": n + 1,
                    "task": task,
                    "seed": "intentional pure consumer fault AFTER real effect; no initial model call",
                }
                started = time.monotonic()

                def deadline(signum, frame):
                    raise TimeoutError("operator recovery-test deadline (240s)")

                previous = signal.signal(signal.SIGALRM, deadline)
                signal.alarm(240)
                try:
                    row["result"] = runtime.run(task, bundle=seed).to_dict()
                except Exception as exc:
                    row["exception"] = {"type": type(exc).__name__, "message": str(exc)}
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, previous)
                    dialogue.flush(final=True)
                    store.close()
                    provider.set_session_key(None)
                row["elapsed_seconds"] = round(time.monotonic() - started, 3)
                row["receipts"] = runtime.trace.records
                result = row.get("result", {})
                row["passed"] = (
                    result.get("status") == "completed"
                    and result.get("value") == expected
                    and len(row["receipts"]) == 1
                    and row["receipts"][0]["status"] == "returned"
                    and (workspace / "input.json").read_text() == original
                    and any(
                        r.get("kind") == "local_execution" and r.get("status") == "fault"
                        for r in result.get("reports", [])
                    )
                    and (
                        name != "publish"
                        or (workspace / "published.txt").read_text() == f"public note {n + 1}"
                    )
                )
                save(case / "result.json", row)
                results.append(row)
                save(output / "results.json", results)
                print(
                    json.dumps({k: row[k] for k in ("case", "round", "passed", "elapsed_seconds")}),
                    flush=True,
                )
    finally:
        leaks = [
            str(p.relative_to(output))
            for p in output.rglob("*")
            if p.is_file() and key.encode() in p.read_bytes()
        ]
        print(
            json.dumps({"phase": "credential_scan", "saved_secret": bool(leaks), "files": leaks}),
            flush=True,
        )
        if leaks:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
