# Engineering Round 53 — nested wait budget and union contracts

## Scope

Nested delegation exposed two generic boundary problems:

1. `wait_agents` was capped at 60 seconds even though a child may itself
   compile and run a nested child. A parent could receive an unavailable view
   while the child was still making legitimate progress.
2. Contract descriptors such as `object|null` and `int or null` were treated as
   one opaque type. A real object was therefore blocked even when it matched an
   explicitly declared alternative.

The wait limit is now 300 seconds (the default remains short and callers can
choose the bound). The contract observer accepts only explicit union branches,
including structured `type` lists, and still blocks values matching none of
the branches. No result is synthesized and no review/evidence gate is bypassed.

## Offline verification

- Projected-observe, coordinator recovery, general, collaboration, handoff,
  and kernel tests: **116 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and
  `git diff --check` passed.
- Added regression coverage for `object|null` and `int or null` output
  contracts and for the 300-second wait schema boundary.

## Live API validation

### Before the change

Run: `/private/tmp/flora-round50-dsv4flash-nested-five`

With the 60-second wait cap, the parent reached a pending child view and then
read a missing result field. The runtime correctly raised `MISSING_KEY`; the
repair exhausted on malformed JSON before the five-layer task completed.

### Wait-budget change

Run: `/private/tmp/flora-round51-dsv4flash-nested-five-wait300`

The same class of task reached the fifth layer in about 312 seconds. Every
layer was actually spawned and read to `next_offset=null`, but the host
contract check found a real `object|null` mismatch and the root returned
`blocked` with the arithmetic observation and limitations preserved. This
confirmed that the larger wait window exposed deeper work without accepting a
nonconforming child.

### Union-contract change

Run: `/private/tmp/flora-round52-dsv4flash-nested-union`

The run no longer stopped on the prior union-type violation. It returned a
fully collected L2 result, but that child exhausted its configured 450-second
wall budget while attempting further recursive delegation. The parent marked
the child `blocked`, recorded the actual digest and the unavailable deeper
evidence, and did not invent an answer. This is progress in semantic handling,
not evidence that deep nesting is broadly reliable.

## Remaining limitations

- Child budgets are independent and currently 450 seconds; a five-plus-level
  chain can still exhaust that budget before its descendants finish.
- Models may still dereference unavailable views or generate invalid programs;
  the runtime rejects these safely but cannot guarantee repair within the
  model-call budget.
- The live nested runs used DeepSeek-v4-flash. Cross-model nested reliability,
  factual verification of returned arithmetic, and larger fan-outs remain
  unverified.
