# Engineering Round 55 — consistent observe-envelope lowering

## Scope

The compact five-layer evaluation generated the same unambiguous tool outcome
envelope (`effect` plus `success` and `error`) in dictionary-shaped source
programs. The block-list frontend already normalized that envelope when the
source was a labelled list, but dictionary-shaped programs reached IR
validation unchanged and failed before the first effect.

The frontend now applies the same normalization to both source shapes. It only
changes the opcode of the complete, explicit outcome envelope to `observe`; the
existing observe signature checks, continuation lowering, tool authorization,
contracts, evidence, and runtime behavior remain unchanged. Incomplete or
ambiguous terms are still rejected.

## Offline verification

- Frontend, projected-observe, coordinator recovery, collaboration, general,
  handoff, and kernel tests: **133 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and
  `git diff --check` passed.
- Added a regression for dictionary-shaped programs using the complete outcome
  envelope.

## Live API validation and cross-output comparison

### Before this change

Run: `/private/tmp/flora-round54-dsv4flash-nested-compact`

The compact five-layer task failed before any tool call. The compiler saw an
`effect` term with `success`/`error` fields and rejected it as missing the
ordinary `resume` field. This was the dictionary-source form of an envelope
that the list frontend already handled.

### After this change

Run: `/private/tmp/flora-round55-dsv4flash-nested-frontend`

The same task reached real delegation: root spawned L2, waited 300 seconds,
read its result, and reviewed it with the actual digest. The nested chain ran
far enough to return a concrete blocked L2 result with the arithmetic answer
observed as `4`; L2 reported that its L3 contract/review did not pass, and the
root preserved that limitation. No compiler format failure occurred and no
child was accepted by coercion. The run completed in about 288 seconds with
three model calls and six tool calls.

This is a boundary execution improvement, not proof of successful five-level
completion. The remaining block is in the model-authored nested contract and
review logic, which correctly remains visible.

## Remaining limitations

- Deep nested tasks can still fail from child budgets or model-authored
  contract/review mismatches.
- The live comparison used DeepSeek-v4-flash; GLM nested behavior remains
  unverified after this frontend change.
- Factual correctness and `claims_verified` remain separate from collection and
  contract status.
