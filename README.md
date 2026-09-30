# Flora

**Act to get things done—and to find things out.**

Flora is a terminal agent for research, files, documents, spreadsheets and external tools. Its own execution kernel compiles model proposals into programs, chooses actions that can both make progress and resolve uncertainty, and synthesizes local contracts from actual consumer checks.

## Start

Python 3.11+ · Linux / macOS / Windows with WSL · Apache-2.0

```bash
git clone --branch general-agent https://github.com/wongzbb/Flora.git
cd Flora
pipx install '.[general,mcp]'
pipx ensurepath
```

Open a terminal in your working folder and run:

```bash
flora
```

Enter the Base URL, API Key and Model without surrounding quotes. The key is hidden and held only in memory. When the endpoint supports `/models`, Flora displays its advertised model IDs. The current directory becomes the workspace and the session name is generated automatically.

```bash
flora -C /path/to/workspace
flora --resume
flora --resume SESSION_ID
flora --config /path/to/profile.json
flora --no-subagents
```

By default the general agent and its workers have unlimited cumulative model/tool/token/time budgets. Usage is still recorded. Individual requests, pure computations, output sizes, storage, concurrency and repeated no-progress loops remain bounded. Explicit profile limits are honored across resume. Limits in other application entry points are independent.

## Conversation

| Command | Purpose |
| --- | --- |
| `/attach PATH` | Attach a workspace file to the next task |
| `/agents` | View real worker identities, tasks, dependencies and state |
| `/agent ID [OFFSET]` | Read a worker result and follow its next offset |
| `/resume-agent ID` | Explicitly resume the same unfinished worker task |
| `/work` | Inspect durable task goals and evidence references |
| `/status`, `/history` | Inspect task state, cumulative usage and conversation |
| `/log [CURSOR]` | Page actual model prompts/output and tool dialogue |
| `/sources`, `/artifacts` | Inspect observations and current artifact hashes |
| `/resume` | Continue the same unfinished task and journal |
| `/new`, `/exit` | Create a conversation or save and exit |

Enter sends; Alt+Enter inserts a newline; Tab completes commands. Ctrl+C requests a pause at the next safe boundary. Model streams and tool calls/results are shown with main/worker labels. Execution waits for strict validation of the complete proposed program.

## Collaboration and continuous work

Each worker runs the full Flora kernel with its own programs, contracts, journal and usage ledger. Read-only workers receive explicit context and can consume completed dependencies. They cannot write files, run commands, delegate recursively, or issue service/browser mutations. The main agent performs mutations centrally.

The default quota is eight workers **per user task**, with three running concurrently. Identical current-task handoffs reuse their saved identity rather than starting duplicates. Collect the complete result, inspect relevant sources, and review required workers before final return. Review checks result collection and reference integrity; it does not prove factual correctness.

Complex tasks can retain required goals, checked source/file references and explicit limitations in a durable work ledger. Scheduling slices preserve the same candidate continuations, trace anchors, budget counters and contract examples. Waiting for required workers does not generate model polling. Resume does not replay completed effects or refund usage.

## Tools

| Capability | Details |
| --- | --- |
| Web research | DuckDuckGo, Brave, Tavily and SearXNG; stable source IDs and retained page content |
| Network routes | Explicit HTTP CONNECT proxy, HTTPS JSON DNS resolver and configured search fallbacks; public-destination checks and redirect protection remain enforced |
| Documents | PDF, DOCX, XLSX, CSV and UTF-8 text; exact filtering and aggregation |
| Delivery | Checked source references, Markdown reports, DOCX/PDF/XLSX exports and real publication hashes |
| Files | Separate create, full replacement and line append; existing-file edits require an observed full hash |
| Integrations | Explicitly granted MCP tools, HTTP services and optional Playwright browser |
| Skills | Workspace guides with pinned content fingerprints |

`--allow-commands` explicitly grants main-agent command execution. Tool/path restrictions are application controls, not an operating-system sandbox. Credentials are supplied through hidden terminal input or designated environment-variable names, never committed in profiles.

## Programs and recovery

Programs use labelled blocks and explicit success/error consumers, then lower mechanically into the original IR. The compiler prompt includes candidates, diagnostics, hypothetical forecast witnesses, pure migrations and the PASS/FAIL/UNKNOWN contract rules. Nested pure expressions are syntax sugar; the frontend never invents observations or inserts tool calls.

Large receipt payloads can be withheld from a compiler prompt with an explicit digest/index notice; the complete journal remains available through `read_receipt`. Model transport, malformed programs, explicit limits, no-progress loops and unknown effects have distinct recovery advice. Unknown external outcomes stop execution and require external evidence; no automatic action replay is performed.

A returned answer, a reviewed worker and a locally passing consumer check are separate from task correctness. Blocked sources and unresolved work must be reported. Arbitrary-model or arbitrary-task success is not guaranteed.

## Documentation

- [Terminal guide](https://github.com/wongzbb/Flora/blob/docs/docs/30-terminal.md)
- [Collaboration, continuous work and recovery](https://github.com/wongzbb/Flora/blob/docs/docs/31-reliability.md)
- [Configuration](https://github.com/wongzbb/Flora/blob/docs/docs/26-general-configuration.md)
- [Full manual](https://github.com/wongzbb/Flora/tree/docs) · download `manual.html` for offline reading
- [Kernel](https://github.com/wongzbb/Flora/tree/core) · [Coding agent](https://github.com/wongzbb/Flora/tree/coding-agent) · [Overview](https://github.com/wongzbb/Flora/tree/overview)

Documentation and HTML live on the `docs` branch. `terminal/` implements the CLI; `general/` implements the application, work ledger and collaboration; `agent/`, `engine/`, `language/`, `checks/` and `state/` implement the execution kernel. Saved sessions retain their pinned tools, configuration and protocol; use a new session to select different capabilities.

## Validation

```bash
python -m unittest discover -s tests -q
```

The regression suite uses deterministic providers and real local journals. Explicit `tests/live_*_probe.py` scripts call real model APIs with hidden key input and verify actual files, outputs, collaboration and diagnostic behavior. `live_reliability_probe.py` supports several model IDs and repeated holdout fixtures. Keep results outside the repository. Research findings require human review; runtime completion alone is never scored as task success.
