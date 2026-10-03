# Engineering Round 60 — nested child idle-window handling

## Scope

Round 59 showed a nested child ending with `output_idle_timeout` while its provider profile allowed a 240-second first-program window and only a 60-second inter-chunk progress timeout. This round changes only the copied provider used by nested children:

- the child progress timeout is at least half of its configured first-program timeout;
- it never exceeds the provider total timeout;
- the parent provider object is unchanged;
- incomplete output remains a transport failure and is never accepted.

This preserves bounded execution and the dual-control rule: an observation is retained only when a complete child result and a host review support the next action.

## Changes

- Added `_child_provider` in `src/flora/general/delegation.py`.
- Nested OpenAI-compatible providers now copy the provider and derive a fairer inter-chunk guard from the first-program window.
- Added tests for parent isolation and total-timeout capping.

## Offline verification

Passed:

- 106 collaboration, streaming, general-agent, recovery, and coordinator-recovery tests.
- `ruff check src tests`
- `python3 -m compileall -q src tests`
- `git diff --check`

## Real API verification

Model: `deepseek-v4-flash`, configured test endpoint, `FLORA_MAX_DEPTH=5`.

Task: three-level nested chain with complete child reads, result-digest review, and object contracts.

Observed:

- Root compiled and executed spawn, wait, read, and review actions.
- L3 actually completed and returned `{layer:3, answer:4, child:null}`; this crossed the earlier child idle-timeout failure.
- One generated review call used an empty `result_digest`; the host rejected it with a missing-key fault. A later program used the actual digest.
- L2's host contract observation was `not_applicable`, so L2 was blocked rather than accepted. Root propagated the limitation and did not invent an answer.
- No incomplete child was accepted and no side effect was replayed.

This is a real improvement in child execution availability, but not an end-to-end success. It also shows that nested contract synthesis remains a separate blocker after transport reliability improves.

## Limitations

- Contract observations can still be `not_applicable` when a model's nested output does not satisfy a composable contract; this must remain blocking until the contract is revised or evidence is sufficient.
- Models may still omit required review arguments, such as `result_digest`; the host rejects these safely, but recovery costs another compilation.
- The run used DeepSeek only. GLM previously returned HTTP 503 before execution.
- No broad reliability claim is made.

