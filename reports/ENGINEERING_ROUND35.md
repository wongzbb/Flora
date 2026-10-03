# Engineering Round 35 — semantic observation frontiers

## Scope

Round 34 bounded a repeated-wait failure by pausing a busy child after three identical observations. Round 35 exposed a precision problem in that guard: the observation digest included accounting, timestamps, event identifiers and internal trace cursors. Those fields changed on every poll, so a wait with no semantic progress could look new and consume the wall budget.

The runtime now derives the `consumer_check` observation digest from the last tool, arguments and receipt after recursively removing volatile accounting and cursor metadata. Status, values, errors, result payloads, tool arguments and other semantic fields remain in the digest. Thus an actual child transition can reset the no-progress counter, while repeated polling of the same semantic state remains bounded. This keeps the dual-control behavior: a tool action both advances the world and exposes an observation frontier that controls the next action. The digest is a control signal, not a completion proof.

## Offline verification

- General, kernel, collaboration, recovery, handoff, projection, frontend and reliability tests: **155 passed**.
- Added an invariant showing accounting metadata is ignored while a status change remains observable.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation and output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-pro`. Task: a three-level nested chain where the leaf computes `19-12`, each parent waits for and reviews its child, and the numeric result is returned through all levels.

Run: `/private/tmp/flora-round35-dsv4pro-nested3-semantic`

- Completed with value **7**, 6 tool calls and 5 model calls in approximately 258 seconds.
- The trace contains spawn, wait, review, read and update phases. The first `update_work` attempt was rejected because the required goal is immutable; the model observed that error, corrected its program, and completed the task.
- Completion checks were ready, with no pending workers. `claims_verified=false` is retained because this arithmetic task has no external source claim to verify.
- Compared with the pre-normalization live run `/private/tmp/flora-round35-dsv4pro-fivelevel`, which repeatedly changed its digest on volatile metadata and ended `needs_program` after approximately 815 seconds, the current run reaches completion without treating accounting changes as information gain. This is a cross-output comparison, not evidence of broad five-level success.

## Limitations

- Five-level nested completion is still not established. A prior five-level DeepSeek Pro run reached a safe `stalled` result after the repeated-wait guard, while another pre-normalization run exhausted its wall budget.
- Semantic digesting detects repeated observations but does not establish task correctness or decide whether a changed observation is useful.
- Model-generated programs can still fail language/contract validation, as shown by the immutable-goal recovery in this run and prior GLM runs with invalid nested programs.
- The default path and models other than DeepSeek Pro still need independent live evaluation.
