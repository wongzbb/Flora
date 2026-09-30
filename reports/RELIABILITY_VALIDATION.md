# Flora reliability verification

Date: 2026-09-30. Source baseline: `general-agent` at
`01e6a7a0103ee3b7fc174b4ec8bea2ee241f946d`. Package version: `0.1.0`.
Environment: Linux, CPython 3.12. This record describes checks actually run;
it does not certify arbitrary models/tasks or production deployment.

## Regression and core behavior

`PYTHONPATH=src python -m unittest discover -s tests -q` ran 189 tests in
285.272 seconds: 188 passed and one native macOS RSS check was skipped on Linux.
Ruff and `git diff --check` passed. Both editable installation with
`[general,mcp]` and an independent wheel installation with the declared extras
succeeded; `pip check` reported no broken requirements. The installed CLI reports
`Flora 0.1.0`.

Covered checks include:

- Explicit worker context, observed source/file references, dependency results,
  dependency failure, current-task quotas, exact handoff deduplication, complete
  pagination, result-digest review, stale evidence, original parent context,
  independent budgets and saved-session identity.
- A generated parent program spawning two full-kernel workers, collecting and
  reviewing their actual results, inspecting original files and returning the
  observed total. This uses a deterministic provider, not a real model.
- More than 32 scheduling steps without restarting a task or recompiling merely
  for a slice; explicit cumulative limits still hold across resume.
- No-progress compilation, repair of repeated *known* effect errors, legitimate
  error consumers, unknown-outcome stops, completed-effect non-replay and charged
  model repair/transport attempts.
- Proxy pinning, configured HTTPS JSON DNS, private/synthetic-IP rejection,
  explicit search fallbacks and refusal to replay uncertain external mutations.
- Strict generated-program validation, disclosed compiler-view omissions,
  byte-for-byte canonical serialization and full journal retention.
- Contract witnesses and PASS/FAIL/UNKNOWN, pure migration checks, scoped
  forecasts, stale anchors, diagnostic constraints and diagnostic ablation.

The dual-control scheduler, effect executor, diagnostic evaluator, contract
synthesis/checking and reuse implementation files retain the baseline contents.
Runtime/API changes add operational slicing and recovery boundaries; they do
not replace the kernel with a separate agent loop or treat worker agreement as
ground truth. Compiler examples retain candidates, diagnostics, hypothetical
witnesses, revisions and the local contract rules. Legacy session protocols keep
their pinned identities.

## Installed terminal integration

A separately installed wheel, with `PYTHONPATH` removed, ran the actual `flora`
executable through seven continuous turns against a deterministic loopback
Chat Completions service. All seven passed: greeting, workspace observation,
JSON aggregation, hash-checked append, fresh readback, missing-file fallback,
and another greeting. Exactly seven model requests occurred, one per turn;
actual files, recorded effects, history and the unchanged input fixture were
checked. The process exited normally and its saved files contained no supplied
fixture credential.

The loopback responses were authored test programs. These checks establish CLI,
transport, execution, file and session integration; their timing is **not** real
model latency or real-model success. Two earlier harness trials failed because
the temporary test service extracted the task marker incorrectly; those were
test-service failures, not counted as successful runs.

## Real API attempts — blocked

The user-authorized gateway was queried directly without inherited proxies.
Both `/v1/models` and `/models` returned connection refusal (`errno 111`) from
this execution environment. Hidden input supplied the credential in memory;
it was not saved in profiles, reports, command arguments or environment values.

The normal GeneralAgent/provider/compiler path was then attempted with these
explicit request model IDs:

| Requested model ID | Case | Result | Tool effects |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | greeting | `needs_program`, transport failure | 0 |
| `deepseek-chat` | greeting | `needs_program`, transport failure | 0 |
| `glm5.3` | greeting | `needs_program`, transport failure | 0 |
| `qwen-plus` | greeting | `needs_program`, transport failure | 0 |
| `kimi-k2` | greeting | `needs_program`, transport failure | 0 |
| `gpt-4o` | greeting | `needs_program`, transport failure | 0 |

Each trial recorded three model request attempts and three unknown-usage calls
under the bounded compiler transport policy. Reserved output accounting is not
evidence that tokens were generated or billed. No model response was received,
no tool was dispatched and all input fixtures stayed unchanged.

The model catalog was unavailable: these are attempted identifiers, **not** a
verified list of currently offered models or a claim about underlying weights.
Connection refusal cannot establish whether the gateway is stopped, restricted
or otherwise unavailable from this environment. Switching the model field does
not test model behavior until a connection and response succeed.

**Real multi-model task acceptance remains unverified.** A reachable gateway is
required to run greeting/tool/multi-agent/research trials. The opt-in
`tests/live_reliability_probe.py` supports repeated holdout fixtures and multiple
models; a completed runtime is assessed separately from task success, and open
research requires human review.

## Documentation and delivery

The standalone manual builds from 32 source chapters. Chapter IDs are unique and
internal navigation targets resolve. Reliability, work/worker tools, current
defaults, recovery and explicit proxy/DNS/search configurations are documented.
HTML/manual sources stay on `docs`, separate from `general-agent`.

Test sources remain in GitHub for reproducibility and are excluded from the
delivery ZIP, along with developer environments, session journals, credentials,
generated fixture files and the separate HTML manual.

Native Windows is not a supported workspace implementation; Windows users use
WSL. Native macOS execution, unrestricted live web search, authenticated external
search providers, native Chromium, production MCP/services and cloud/multi-tenant
load testing were not newly verified here. These checks do not establish a
success rate for thousands of unrelated users or tasks.
