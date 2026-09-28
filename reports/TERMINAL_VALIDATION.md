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

**546 unique automated checks passed:**

| Suite | Checks | Coverage |
| --- | ---: | --- |
| Existing kernel/application regression | 401 | Compiler, IR/VM, scheduler, contracts, diagnostics, persistence, tools, provider behavior and application integration. Two default-entry expectations were updated to the requested terminal routing. |
| General application regression | 59 | Document parsing/exports, table arithmetic, receipts, report guards, safe paths, source quotas, MCP stdio and Streamable HTTP, unknown outcomes and optional browser adapter lifecycle. The retired UI-server test is not included. |
| Terminal and delegation suite | 38 | Guided connection, credentials, model list fallback/pagination/redirects, directory isolation, automatic IDs, history/context resume, attachments, child execution and recovery, output sanitization and terminal presentation. |
| Transport, repair and dialogue regression | 27 | SSE completion/usage/UTF-8/keep-alives, partial-stream rejection, HTTP and timeout classification, bounded retry accounting, compatibility negotiation, precise JSON repair windows, stale-anchor rejection, same-budget resume, previous-distribution checkpoint resume without repeated writes, real child dialogue, transcript redaction/retention and another real PTY interaction. |
| Unlimited-budget defaults | 21 | True null limits, independent finite caps, zero-budget semantics, usage and interrupted reservations, 34 main-agent calls across reopening, a 10-call child, legacy finite-session compatibility, unchanged low-level defaults, live UI mode labels, diagnostic selection and unknown-effect non-replay. |

Three of the 38 new checks use real pseudo-terminal processes with prompt-toolkit and Rich: hidden credential entry and Ctrl+D; Alt+Enter multiline submission followed by the resume picker; and Ctrl+C before a file effect, then explicit resume with exactly one file write. These are executable terminal interactions, not mocked input-widget assertions.

The complete parent-to-subagent test sends real HTTP Chat Completions requests to a deterministic local fixture, compiles and executes a parent program that starts two child agents, runs their independent kernels and file tools, waits for them, collects their actual results, then reopens the persisted session. Model output is scripted in this test; it is not a paid-model quality evaluation.

## Packaging and installation

- A wheel and source distribution were built successfully.
- The wheel was installed in a separate Python 3.12 environment with the selected general, MCP and browser extras.
- All 86 terminal/delegation, recovery and unlimited-budget checks also passed against that installed wheel, including subprocess and PTY launches without the source tree on their import path. This repeated installation run is not counted again in the 546 total.
- All three terminal asset files are present; obsolete Web UI/server files are absent.
- `pip check` reports no broken requirements; Ruff and patch-whitespace checks pass for the changed implementation.
- Tests, test dependencies, model journals, credentials, developer environments and build intermediates are excluded from the published repository contents and final source ZIP.

## Kernel continuity and limits

The IR, VM, scheduler, effect executor, contracts, diagnostics, reuse, opaque state, session and trace implementations remain unchanged from general-agent baseline tree `16f8606cdce958e24e2b957d4ff320f3e9a68977`. The budget ledger accepts null for an uncapped limit while retaining its original finite low-level defaults; the runtime diagnostic guard handles an unlimited tool budget without disabling diagnostic selection. GeneralAgent resolves unspecified new-session limits to null and pins that policy in the saved profile. The provider reads SSE or JSON with explicit error categories; the compiler adds an opt-in, accounted transport recovery policy and better syntax repair context. The general application enables streaming, JSON preference, two shared recovery calls and human dialogue logging. Low-level provider complete still sends exactly one POST; low-level LLMCompiler has no transport retries by default. No evidence anchors, contract checks, tool-effect recovery rules or budget accounting are weakened.

A dedicated compatibility check creates an unfinished task using the previously installed distribution, after a successful file write and failed subsequent compilation. The current implementation reopens that session with its original identity and budget, consumes the existing receipt and completes with exactly one total file write.

Subagents use separate usage ledgers, not the parent's ledger. New general sessions default to unlimited cumulative model/tool calls, input/output tokens and wall time for both parent and children. Explicit limits remain enforceable. Defaults still allow three concurrent children and eight total children per conversation. Existing sessions retain their saved policy and usage; no fingerprint or old cap is silently rewritten. Children are read-only and cannot recursively delegate, execute commands or invoke MCP/browser/service mutations. This is local bounded coordination, not a distributed agent scheduler or sandbox.

## Authorized live-provider check

This transport smoke test was run before the general budget-default change. The budget-only changes were validated with deterministic execution and persistence tests; no additional paid-model request was needed.

The user-authorized OpenAI-compatible relay and `deepseek-v4-flash` completed a real task: read evidence.txt and state the observed project name and count. The actual answer reported Flora and 41 correctly. Elapsed time was 70.74 seconds: three model calls, one file tool call, 20,713 reported input tokens and 7,246 reported output tokens; no unknown-usage calls. One validation repair occurred before completion. The captured dialogue included actual provider reasoning_content, program text, repair feedback, tool arguments and tool results. The API credential was absent from the retained transcript.

The /models discovery request timed out during this run; explicitly selecting the known model ID still allowed Chat Completions to succeed. This is a live transport/compiler/tool smoke test, not a public-web research benchmark or a guarantee of model reliability. Credentials, raw private session files and test code are excluded from the release.

Prior real-model general-application results are recorded separately in GENERAL_VALIDATION.md. Its native-browser and unrestricted public-search limitations still apply. Verification ran on Linux; macOS and WSL were not separately executed, and native Windows file-tool support is not claimed.

Actual model text is streamed to the terminal when returned by the provider. reasoning_content is displayed only when the provider supplies it. Programs are never executed before full stream completion, strict JSON/IR validation and anchor checks. Human transcripts have a separate 8 MiB rolling quota and redact known credentials before display/persistence. They are not authoritative tool receipts or unrestricted retention of arbitrary business secrets.

Ctrl+C requests a pause before the next model or tool dispatch; an in-flight request may finish or time out first. Each compilation permits at most two extra transport/capability recovery requests shared across the original attempt and the single format repair. Every POST consumes the same budget ledger; unknown usage conservatively consumes the output reservation. Permanent credential/access failures are not retried. Resuming keeps the original model, endpoint, capabilities and spent budget.
