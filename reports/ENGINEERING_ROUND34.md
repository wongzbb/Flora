# Engineering Round 34 — stopping repeated waits safely

## Scope

Round 33 exposed a scheduler-level no-progress hole. A five-level DeepSeek Pro task repeatedly executed the exact same `wait_agents` request while a child remained busy. The prior guard deliberately avoided pausing busy children, so the parent consumed the full 900-second wall budget without gaining information.

The guard now treats an identical wait program and identical observed wait value as no information gain even when a child is running. On the third repetition it requests a pause from the child coordinator, which stops children at their next safe boundary, and returns a resumable `stalled` result. No child answer is invented, no effect is replayed, and explicit resume remains required. If the observed wait value changes, the counter resets and normal progress continues.

## Offline verification

- General, kernel, collaboration, recovery, handoff, projection and frontend tests: **147 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.
- Added regression coverage for repeated observations and safe pause propagation while a child is busy.

## Live API validation and before/after comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-pro`. Task: five-level nested single-chain computation with contract/review requirements and no side effects.

### Before this change

Run: `/private/tmp/flora-round33-dsv4pro-fivelevel`

- Five child levels were started and the leaf completed.
- Parent/child collection then repeated the same wait phase while intermediate children exhausted or paused.
- The root ended `budget_exhausted` after approximately 963 seconds, with 15 tool calls and no verified final value.

### After this change

Run: `/private/tmp/flora-round34-dsv4pro-fivelevel`

- The root recognized the repeated identical wait observation and returned `stalled` after approximately 372 seconds, with 3 tool calls and 2 model calls.
- The child coordinator was asked to pause the running child; the result records `failure.code=no_progress`, keeps the task unfinished, and leaves `completion_checks.ready=false`.
- No child result was promoted to success and no side effect was replayed.

This is a cross-output behavior change: the same class of nested failure now terminates at a safe, resumable boundary instead of consuming the entire wall budget. It does not claim that the five-level computation succeeded.

## Limitations

- Five-level nested completion remains unproven; this round improves bounded failure handling and resumability.
- A changed value can still be unhelpful semantically; the guard detects exact repetition, not correctness.
- Model-generated programs may still fail JSON, block, or contract validation.
- No external factual claims were established; `claims_verified=false` remains correct.
