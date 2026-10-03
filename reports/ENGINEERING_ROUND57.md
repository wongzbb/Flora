# Engineering Round 57 — authoritative nested review evidence

## Scope

This round keeps the dual-control and synthesizable-contract design intact while removing an ambiguity in nested collaboration guidance.

The host review tool returns a structured observation under:

`review_agent -> review -> contract_check.status`

A child result may also contain a field named `contract_check`, but that field is a worker claim. The coordinator guidance and tool result schema now explicitly distinguish the two. Parents must use the host review observation for acceptance/blocking decisions, while still treating review as collection/contract evidence rather than proof of factual truth.

This is a boundary clarification, not a model-specific JSON workaround. It applies to the semantic object returned by the host tool regardless of how the model formats its proposed program.

## Changes

- Added an explicit `review_agent` result description in `src/flora/general/schemas.py`.
- Updated generic coordinator instructions in `src/flora/general/coordinator.py` to require `review.review.contract_check.status` (the returned shape is `review.contract_check.status`) and reject substitution with a child value field.
- Added a regression assertion that the exposed host tool description contains the authoritative-observation guidance.

## Offline verification

Passed:

- 134 targeted unit tests covering frontend parsing, projected observations, recovery, collaboration, general coordinator behavior, handoff authority, and kernel invariants.
- `ruff check src tests`
- `python3 -m compileall -q src tests`
- `git diff --check`

## Real API verification

Endpoint: configured Flora test API, model `deepseek-v4-flash`, `FLORA_MAX_DEPTH=5`.

Task: execute a five-level nested chain (root -> L2 -> L3 -> L4 -> L5), with each layer waiting for the complete child result and calling `review_agent`; L5 computes 2+2.

Observed before the final root phase failed:

- The real child chain reached L5 and returned answer 4.
- L2's complete read view contained the full nested value through L5.
- The host review observation reported contract status `pass`.
- The nested completion record reported ready and the required child count was satisfied.

The root then attempted to produce its final program, but the compiler rejected both repair attempts because the generated output was not strict JSON (duplicate/non-finite-value validation). The run ended as `needs_program`; no final root `review_agent`/publish phase completed.

Therefore this run demonstrates real five-level execution and authoritative host review evidence, but it is **not** an end-to-end task success. It also shows that final compiler robustness remains an independent blocker.

## Limitations and next work

- The live result used DeepSeek only; GLM cross-model confirmation is still pending.
- The final phase can still exhaust compiler repair attempts after successful nested side effects.
- Review continues to validate collection/contract conditions, not factual truth; source/evidence checks remain required.
- No broad reliability claim is made from this single live run.

