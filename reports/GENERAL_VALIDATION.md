# Flora General Agent verification

Scope: this report records verification of the general-application baseline at commit `3528e13796f8671d8fead2f5f207088715bcd038`. Its Web UI measurements are historical; current terminal behavior, removed Web packaging, credential changes and independent subagents are covered by [TERMINAL_VALIDATION.md](TERMINAL_VALIDATION.md).

This report distinguishes application implementation, deterministic checks, real-model tasks and environment-limited checks. It does not claim universal task success, a production security audit or load-test certification.

## Implementation and kernel continuity

The general application adds CLI and local Web entry points, source/artifact persistence, bounded document parsing, decimal table operations, cited reports and document exports, search/fetch, explicit HTTP services, skills, MCP and a Playwright adapter.

The compiler, IR/VM, runtime, scheduler, effect executor, budget, contracts, diagnostics, reuse and kernel session/trace files are unchanged from the coding-agent baseline `b4adadaca711be07ba0c353b9e12ccbbbca46043`. The only existing Python source changed is the CLI registration. `GeneralAgent` calls the existing `Agent.run` and `Agent.resume`; it does not replace dual control or synthesized local contracts with another execution loop.

The application code is independently implemented within Flora. Hermes and Deep Agents source files were not transplanted. Optional third-party packages are installed as dependencies with their own distribution licenses.

## Automated checks

- 401 existing kernel/application regression tests passed.
- 60 general-application checks passed: document formats and exports, precise table aggregation, citations, source quotas, path and symlink boundaries, session locks and identity, actual MCP stdio and Streamable HTTP services, timeout/unknown outcomes, guarded HTTP actions, schemas, pause/resume and offline status.
- Six of those 60 checks use an explicitly simulated browser driver to test the adapter's lifecycle, stale references, action grants, screenshot registration and unknown outcomes. They are not counted as native Chromium tests.
- 14 DOM/UI integration assertions passed against the actual loopback HTTP service and Flora kernel using a deterministic model fixture. They cover authentication, token removal, source pagination, untrusted text rendering, uploads, task execution, progress, artifacts and download content. The DOM implementation is JSDOM; this is not a screenshot/layout validation claim.
- A malformed model evidence anchor is rejected without dispatching tools or refunding usage, then exposed as an explicit resumable needs_program result.
- New Python modules pass Ruff checks. The inline JavaScript passes Node syntax checking.

A wheel was installed into a fresh Python 3.12 virtual environment with its optional dependencies. The installed package passed the general integration suite, packaged Web asset checks, CLI smoke commands and pip dependency checks.

Test source files, fixtures, credentials, model-session journals and developer environments are excluded from the repository delivery and source ZIP.

## Real model execution

The user's authorized API was used with `deepseek-v4-flash`, `thinking.type=enabled`, `reasoning_effort=low`, JSON-object response mode, a 16,000-token compiler output allowance and one permitted format repair. No weights were trained or changed. Model calls and tool calls use the ordinary shared budget ledger.

| Task | Final state | Model calls | Tool calls | Reported input/output tokens | Output checks |
| --- | --- | --- | --- | --- | --- |
| CSV grouped revenue analysis in Chinese | completed | 2 | 4 | 13565 / 8169 | North 200.00, South 100.00, West 150.00; Markdown + DOCX |
| Read two HTTP pages and distinguish their evidence | completed | 2 | 4 | 14742 / 13899 | Two source IDs; Markdown + PDF |
| Compute 37 × 19 through a real MCP subprocess | completed | 2 | 2 | 13645 / 3877 | Actual tool value 703; cited Markdown report |

All three completed sessions were closed and reopened successfully. Produced artifacts matched their publication hashes. The webpage contents and MCP business service in this integration evaluation were controlled fixtures; the model API, HTTP transport, MCP protocol, parsing, execution and output files were real. This is not an open-web research benchmark.

An earlier configuration with reasoning disabled failed to finish two tasks. It exposed a missing advertised table page bound (the implementation allowed at most 500, while the model repeatedly requested 1000) and model-generated malformed JSON. The schema now advertises the actual bounds; the tested model profile enables reasoning. Those initial failures are not erased or presented as successful runs. These small integration tasks do not establish a statistical success rate.

An additional real-model task was run from the installed wheel: read a project brief, preserve an unknown launch date, and produce Markdown/DOCX. Its first proposal used a stale digest and was rejected before any tool ran. Explicit continuation completed with three total model calls (including the rejected proposal), three tool calls, 20250 reported input tokens and 7636 output tokens. The session reopened and the DOCX matched its recorded hash. The subsequent application check also covers registering ordinary text-file tool outputs for download without labelling them citation-checked reports.

## Explicit verification limits

- Native Chromium was downloaded but exited with SIGTRAP when launched in the current execution environment. The optional browser adapter is implemented and its deterministic lifecycle tests passed; native browsing, clicks and screenshot rendering were not verified here.
- Public-domain DNS resolution was unavailable to the direct, IP-pinned webpage transport in this environment. Search provider protocols were exercised with HTTP fixtures and checked against their public APIs; authenticated live Brave/Tavily/SearXNG services and unrestricted public-web fetching were not tested.
- No paid search credentials, production MCP servers, user browser login sessions, multi-user load tests, Windows native filesystem fallback or production security audit were used or claimed.
- Scanned-document OCR, audio/video understanding and model vision are outside the built-in text/document adapter. Specialized capabilities can be supplied through explicitly configured services.

The source and manual describe these boundaries. A target deployment still needs a working model, the optional service credentials it uses, browser/system dependencies when browser tools are enabled, and appropriate process/network isolation for trusted host capabilities.
