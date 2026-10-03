# Engineering Round 59 — shared final return guidance

## Scope

The previous live five-level run showed a semantic program that repeated the same large nested return literal in several error/success branches. The generated bundle became fragile at a branch boundary and failed strict parsing. This round adds a model-agnostic generation constraint:

- when branches return the same observed result, bind shared observations;
- use one final return block where semantics permit;
- avoid copying large result literals into multiple branches.

This changes only compilation guidance. It does not relax JSON parsing, alter contract checks, infer answers, or accept unverified results.

## Changes

- Added the shared-value/single-final-return rule to the focused compiler prompt.
- Added the same rule to bounded compiler repair guidance.

## Offline verification

Passed:

- 112 targeted tests covering compact prompts, expression lowering, projected observation, recovery, general behavior, and collaboration.
- `ruff check src tests`
- `python3 -m compileall -q src tests`
- `git diff --check`

## Real API verification

Model: `deepseek-v4-flash`, endpoint configured in the task, `FLORA_MAX_DEPTH=5`.

Task: three-level nested chain with wait/read-to-`next_offset=null`, host `review_agent`, and object contract.

Observed:

- Root compiler output was accepted and executed.
- Root spawned L2, waited, read the child, and called `review_agent`.
- The host correctly rejected acceptance because L3 was unfinished: model transport `output_idle_timeout`; no child value was accepted.
- Root then read work, marked the required task blocked with the observed limitation, and returned a blocked result.
- No duplicate side effect was replayed and no unfinished child was reported as completed.

This run does not demonstrate end-to-end success. It demonstrates safe failure propagation under a long-running nested call. The shared-final-return rule itself was not isolated by this run because the downstream child timed out before the final result synthesis.

## Limitations

- Long nested model calls can still hit provider idle timeouts.
- DeepSeek only was available for this run; GLM previously returned HTTP 503 before execution.
- The prior successful five-level child-chain evidence remains valid, but final root synthesis still needs a successful live run.
- No broad reliability claim is made.

