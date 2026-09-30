# Flora

**Act to get things done—and to find things out.**

Flora is a terminal-first general-purpose agent for web research, document and spreadsheet processing, external tool integration, and delivering files and reports.

Run `flora` in any working directory, enter your Base URL, API Key, and Model, and get started. The current directory becomes the workspace, and a session ID is generated automatically. API keys are entered without being displayed and are used only within the current process.

By default, the main agent and subagents in new general-purpose sessions have no cumulative limits on model calls, tool calls, input/output tokens, or session duration. Usage is still tracked, and startup displays `Budget: unlimited`. Per-response output limits, request timeouts, bounded retries, and concurrency safeguards remain in effect. Set explicit budget limits through a profile when needed.

Python 3.11+ · Linux / macOS / Windows WSL · Apache-2.0

## Install Once, Run Anywhere

After installing [pipx](https://pipx.pypa.io/stable/installation/):

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
pipx install '.[general,mcp]'
pipx ensurepath
```

Open a new terminal after updating PATH for the first time. Start Flora in the project or folder you want to work with:

```bash
flora
```

Enter values as prompted, without surrounding quotes. API keys are not echoed or written to configuration files, conversations, or environment variables. If the service supports `/models`, Flora lists the returned models; enter a number or the full model ID. If model listing is unavailable, enter the model ID manually.

The terminal application does not require a browser. For browser automation, install `.[general,mcp,browser]` and Playwright Chromium for your environment. See the manual for details.

## Common Operations

```bash
flora                          # Use the current directory and create a session automatically
flora -C /path/to/project       # Specify a working directory
flora --resume                 # Choose a previous session in the current directory
flora --resume SESSION_ID      # Open a specific session directly
flora --config /path/profile.json
```

Flora prompts for model connection details on every launch, including when resuming a session. Resumed sessions retain their original endpoint, model, tools, and budgets; the API key can be updated. Continue chatting after opening a saved session, or explicitly continue an unfinished task with `/resume`. Start a new session when changing the model or capability configuration. Saved sessions retain the budget settings they were created with. If a resumed session shows finite limits, `/new` uses the current defaults without erasing the previous session's usage records.

Enter a task in the conversation. Press Enter to send, Alt+Enter for a new line, and Tab to complete commands:

| Command | Purpose |
| --- | --- |
| `/attach PATH` | Attach a file from the workspace to the next task; paths may contain spaces |
| `/agents` | View actual subagent tasks, IDs, and statuses |
| `/agent ID` | Read a subagent's actual results |
| `/resume-agent ID` | Explicitly continue an interrupted subtask |
| `/history`, `/status` | View saved conversation history or the current status and budget |
| `/log [CURSOR]` | Page through actual request prompts, model outputs, tool arguments, and results |
| `/sources`, `/artifacts` | View sources and output files |
| `/new`, `/exit` | Start a new session, or save and exit |

During execution, Ctrl+C requests a pause at the next execution boundary; an in-flight request must first return or time out. Press Ctrl+D at the prompt to exit. See the [terminal manual](https://github.com/wongzbb/Flora/blob/docs/docs/30-terminal.md) for detailed commands and recovery rules.

The terminal displays actual model responses and tool interactions, identifying the main agent and subagents. When the model supports streaming, content appears as it arrives. Programs execute only after strict validation. View full request prompts with `/log 0`; `/log` shows the most recent records by default.

The general-purpose entry point requests JSON output by default and uses bounded recovery with explicit usage accounting for transient connection errors, rate limits, and selected server errors. Authentication failures and incorrect model endpoints produce specific explanations. Incomplete programs from interrupted streams are not executed, and tool calls with unknown outcomes are not automatically repeated. See [troubleshooting](https://github.com/wongzbb/Flora/blob/docs/docs/28-general-operations.md).

## Compilation and Recovery

New general-purpose sessions use `block-list-v2` with explicit success/error consumers for `observe`, then lower the source to the original IR for validation and execution. The candidate, diagnostic, and contract mechanisms remain unchanged. Read-only workspace observations, no-progress stream cutoffs, and a deadline shared across the compilation round are included. If the output budget is exhausted without producing a program body, Flora no longer repeats the same format-repair attempt. Existing sessions do not automatically switch protocols or tool fingerprints.

After a consumer fault, the compiler supplies a bounded index of existing real receipts so the model can repair the remaining computation rather than redo the entire task. This is evidence-based recovery guidance, not a deterministic correctness guarantee. These changes do not bypass network protections or guarantee that upstream models and networks are always available.

## Capabilities

| Capability | Details |
| --- | --- |
| Web research | Retained page content and sources; DuckDuckGo, Brave, Tavily, SearXNG |
| Documents and spreadsheets | PDF, DOCX, XLSX, CSV, and text; exact numeric filtering and grouped aggregation |
| Report delivery | Source citation checks, Markdown, DOCX/PDF/XLSX, and file hashes |
| Multiple agents | The main agent can delegate independent read-only subtasks, with up to 3 running concurrently by default; programs, effect logs, budgets, and results are saved separately |
| External systems | MCP stdio / Streamable HTTP; explicitly authorized HTTP services and methods |
| Browser tools | Playwright observation, clicking, typing, and screenshots, subject to configured domain and action permissions |
| Skills | Markdown skills from a designated directory, with pinned content fingerprints |
| Continuous work | Directory-scoped sessions, cross-turn history, pause, and resume |
| Coding tasks | `flora code` validates changes and produces patches in an isolated Git worktree |

Subagents handle reading, research, and analysis by default; the main agent centrally performs file writes and external modifications. Every subagent runs the Flora kernel and is distinct from the kernel's internal candidate programs. By default, each session can create up to 8 subagents, with separately tracked budgets. See the manual for exact limits and permissions.

## Execution Kernel

Model proposals are compiled into inspectable programs. Actions can both advance the task and help distinguish between competing proposals. Actual tool results and consumer checks are used to synthesize local contracts that guide what happens next. Independent subagents use the same mechanism, preserving their own trajectories and unknown outcomes.

Tool permissions and path checks are not an operating-system sandbox. Command execution is available to the main agent only when `--allow-commands` is explicitly enabled. MCP and browser capabilities are enabled through configuration. OCR for scanned documents, audio/video understanding, and model vision input can be integrated through additional services.

## Documentation and Code

- [Quick start](https://github.com/wongzbb/Flora/blob/docs/docs/24-general-agent.md) · [Terminal manual](https://github.com/wongzbb/Flora/blob/docs/docs/30-terminal.md)
- [Full manual and Python API](https://github.com/wongzbb/Flora/tree/docs); download `manual.html` for offline reading.
- [Terminal validation records](reports/TERMINAL_VALIDATION.md) · [General capability validation records](reports/GENERAL_VALIDATION.md)
- [Kernel baseline](https://github.com/wongzbb/Flora/tree/core) · [Coding Agent](https://github.com/wongzbb/Flora/tree/coding-agent) · [Project overview](https://github.com/wongzbb/Flora/tree/overview)

`src/flora/terminal/` implements the terminal interface; `general/` combines application tools and subtasks; `agent/`, `engine/`, `language/`, `checks/`, and `state/` implement the execution kernel; `integrations/` connects models and tools. The full documentation is maintained separately on the `docs` branch.

## Optional Performance Profile

New General sessions default to `general-v3`, `block-list-v2`, and `compact-v1`:
the model generates more concise source programs, and nested pure expressions are mechanically lowered into fresh SSA operations before passing through the original IR validation, scheduling, and contract checks.
The frontend does not evaluate expressions implicitly, generate additional tool calls, or switch to per-tool replanning. Existing v1/v2 sessions retain their original protocols and tool identities.

Endpoints that support DeepSeek reasoning parameters can explicitly use the low-latency profile:

```bash
# Run from this repository, or replace the profile path with its absolute path
flora --config configs/deepseek-fast.json
```

When prompted, enter your own Base URL, API Key via hidden input, and a model ID supported by your endpoint.
The profile's `deepseek-v4-flash` ID is a name advertised by the test gateway, not independent verification of its underlying model weights.
When using another endpoint, confirm that it supports the model ID and reasoning parameters.
The profile contains no API key or fixed gateway address. It enables low-effort reasoning rather than disabling reasoning.
Start a new session; do not impose this profile on an existing session. Running `flora` alone does not automatically load this performance profile.

The v3 file tools explicitly distinguish `create_file`, `update_file`, and `append_lines`. Updates and appends still require the full observed hash,
and writes still produce real receipts. They do not silently overwrite files, bypass path restrictions, or replay effects with unknown outcomes.
Compilation has a shared deadline. Optional stalled-stream recovery discards only unexecuted partial programs; it does not replay tools that have already executed.

**Limitations:** Models and upstream services can still produce errors and long-tail response times. In some proxy DNS environments, existing network policies may still block web research.
This version does not guarantee success on arbitrary tasks. A normal completion status or an honest report of a network failure does not mean that the research task succeeded.

## Regression Tests

From the repository root, use a Python environment with the project dependencies installed:

```bash
python -m unittest discover -s tests -q
```

The default tests do not call real model APIs. `tests/live_*_probe.py` scripts are reusable, manually invoked validation tools
for general tasks, the installed terminal entry point, and consumer-fault recovery. Use `--help` to inspect their arguments.
Explicitly specify the endpoint and output directory; API keys are provided through hidden input. Keep run outputs outside the repository, and do not commit logs or credentials.
