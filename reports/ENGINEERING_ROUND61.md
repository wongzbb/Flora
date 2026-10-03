# Engineering Round 61 — explicit contracts for composable child handoffs

## Scope

Round 60 showed that nested execution could complete while the parent omitted `context.contract`, making host contract checking `not_applicable`. This round strengthens the model-facing contract boundary:

When an assigned child task specifies a return shape/type, evidence or review, or nested workers, the model must provide `context.contract` before spawning. The host still does not invent task-specific fields or coerce results. Ordinary research tasks without a declared interface remain possible.

The instruction is present in both coordinator guidance and the exposed `spawn_agent` tool description. It preserves assume–guarantee composition: inputs, outputs, guarantees, dependencies, evidence requirements, and delegation bounds travel with the handoff and are checked against the observed returned value.

## Changes

- Added explicit contract-before-spawn guidance to coordinator instructions.
- Added the same requirement to the host `spawn_agent` description.
- Added a regression assertion for the exposed tool description.

## Offline verification

Passed:

- 80 collaboration, general-agent, handoff-authority, and coordinator-recovery tests.
- `ruff check src tests`
- `python3 -m compileall -q src tests`
- `git diff --check`

## Real API verification

Model: `deepseek-v4-flash`, configured test endpoint, `FLORA_MAX_DEPTH=5`.

Task: three-level root -> L2 -> L3 chain. Each layer had an explicit object contract, read-to-`next_offset=null`, reviewed the actual result digest, and required the next layer only until L3.

Observed:

- Root and L2 records contained explicit contracts. Root required one direct child; L2 required one direct child; L3 required none.
- L3 returned `{layer:3, child:null, answer:4}`.
- L2's host review returned contract status `pass`.
- Root returned `{layer:1, child:{layer:2, child:{layer:3, child:null, answer:4}, answer:4}, answer:4}`.
- The work ledger was updated to completed after the required child review.
- The first root review program supplied an empty `result_digest`; the host rejected it. A later continuation used the observed digest and completed successfully. This demonstrates that malformed or unsupported claims change the next action rather than being silently accepted.
- Final status was `completed`; `claims_verified=false` remains correctly preserved because review proves collection/contract integrity, not factual truth.

This is an end-to-end success for one three-level task, not a broad reliability claim.

## Limitations

- The run used DeepSeek only. GLM previously returned HTTP 503 before execution.
- A four-plus-level run and the requested seven-child and multi-tool fan-out tasks still need independent live evidence.
- Models can still omit required review arguments on their first attempt; safe host rejection and continuation worked here.
- Contract pass does not prove external factual truth; source/evidence checks remain required.

