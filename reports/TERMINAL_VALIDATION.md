# Flora terminal application verification

Verified on Python 3.12 / Linux. This report covers the default terminal launcher, directory-scoped conversations, ephemeral credentials, model discovery and independent subagents. It does not certify every terminal, model, operating system or external service.

## Delivered behavior

- `flora` opens the interactive agent in the current working directory. `-C` / `--workspace` selects another existing directory.
- Session IDs are generated automatically. The directory-scoped registry provides `--resume` selection and `--resume ID`, including unambiguous ID prefixes.
- Every launch asks for Base URL, hidden API Key and Model. Model discovery displays returned IDs, handles standard pagination and allows manual selection when discovery is unavailable. Redirects never forward the credential.
- The API key stays in process memory, outside persistent configuration, conversation metadata and child-process environment variables. Saved sessions retain nonsecret connection identity and cumulative budgets.
- The overview's original vector flower and character assets supply the terminal pixel art. Terminal colors retain its navy, violet, mint and pink palette. Plain and narrow-terminal layouts are available.
- Main execution and independent read-only subagents have separate displayed identities. Child records, kernel journals, results and budgets persist. A stale queued/running child requires explicit resume; completed work is recovered without another model call.
- The Web operation UI and local UI server are absent from the source package and wheel. The separate overview branch remains a static project page.

## Verification results

**498 unique automated checks passed:**

| Suite | Checks | Coverage |
| --- | ---: | --- |
| Existing kernel/application regression | 401 | Compiler, IR/VM, scheduler, contracts, diagnostics, persistence, tools, provider behavior and application integration. Two default-entry expectations were updated to the requested terminal routing. |
| General application regression | 59 | Document parsing/exports, table arithmetic, receipts, report guards, safe paths, source quotas, MCP stdio and Streamable HTTP, unknown outcomes and optional browser adapter lifecycle. The retired UI-server test is not included. |
| New terminal and delegation suite | 38 | Guided connection, credentials, model list fallback/pagination/redirects, directory isolation, automatic IDs, history/context resume, attachments, child execution and recovery, output sanitization and terminal presentation. |

Three of the 38 new checks use real pseudo-terminal processes with prompt-toolkit and Rich: hidden credential entry and Ctrl+D; Alt+Enter multiline submission followed by the resume picker; and Ctrl+C before a file effect, then explicit resume with exactly one file write. These are executable terminal interactions, not mocked input-widget assertions.

The complete parent-to-subagent test sends real HTTP Chat Completions requests to a deterministic local fixture, compiles and executes a parent program that starts two child agents, runs their independent kernels and file tools, waits for them, collects their actual results, then reopens the persisted session. Model output is scripted in this test; it is not a paid-model quality evaluation.

## Packaging and installation

- A wheel and source distribution were built successfully.
- The wheel was installed in a separate Python 3.12 environment with the selected general, MCP and browser extras.
- All 38 terminal/delegation checks also passed against that installed wheel, including subprocess and PTY launches without the source tree on their import path. This repeated installation run is not counted again in the 498 total.
- All three terminal asset files are present; obsolete Web UI/server files are absent.
- `pip check` reports no broken requirements; Ruff and patch-whitespace checks pass for the changed implementation.
- Tests, test dependencies, model journals, credentials, developer environments and build intermediates are excluded from the published repository contents and final source ZIP.

## Kernel continuity and limits

The compiler, IR, VM, runtime, scheduler, effect executor, budget, contracts, diagnostics, reuse, opaque state, session and trace implementations remain unchanged from general-agent baseline tree `16f8606cdce958e24e2b957d4ff320f3e9a68977`. The Agent API credential precheck now accepts the provider's process-local key. No evidence anchors, contract checks, effect recovery rules or budget accounting are weakened.

Subagents use separate bounded budget ledgers, not the parent's ledger. Defaults allow three concurrent children and eight total children per conversation; every child has at most eight model calls and forty tool calls. Children are read-only and cannot recursively delegate, execute commands or invoke MCP/browser/service mutations. This is local bounded coordination, not a distributed agent scheduler or sandbox.

The new terminal/delegation changes were tested with controlled OpenAI-compatible model responses, not newly charged live-model calls. Prior real-model general-application results are recorded separately in GENERAL_VALIDATION.md. The native browser and unrestricted public-search limitations described there still apply. Terminal validation ran on Linux; macOS and WSL were not separately executed, and native Windows file-tool support is not claimed.

Model output remains buffered through the program compiler; the terminal streams execution events rather than exposing raw reasoning tokens. Ctrl+C requests a pause at an execution boundary; an in-flight model or tool request may finish or time out first. Resuming keeps the original model, endpoint, capabilities and spent budget. Context retention is bounded and omitted turns are reported.
