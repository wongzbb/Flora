# Engineering Round 56 — remove contradictory recursion guidance

## Scope

The runtime already exposed a nested coordinator when depth and delegation
contracts allowed it, but both the base delegation instructions and the
coordinator's collaboration instructions still said that children could not
delegate recursively. In a five-layer task, the model therefore treated the
requested chain as forbidden and planned a blocked result before trying the
available nested tool.

The guidance now states the actual invariant: recursive delegation is allowed
only when the host exposes a nested coordinator and the assigned contract
requires a separable child. Read-only restrictions, mutation restrictions,
depth/quota checks, budget limits, full collection, and review requirements are
unchanged. This aligns the model-facing plan with the host's capability rather
than adding a task-specific recipe.

## Offline verification

- Frontend, projected-observe, coordinator recovery, collaboration, general,
  handoff, and kernel tests: **134 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and
  `git diff --check` passed.
- Added a regression ensuring the delegation instructions no longer contain an
  unconditional recursive-delegation prohibition.

## Live API validation and cross-output comparison

Task: compact five-layer `root-L2-L3-L4-L5` chain with `object|null` and
`number|null` child contracts.

### Before this change

Run: `/private/tmp/flora-round55-dsv4flash-nested-frontend`

The host reached L2, but the model's reasoning repeatedly treated recursive
delegation as forbidden. The returned chain stopped with L3 contract failure;
the model's planning explicitly cited the contradictory “No recursive
delegation” instruction.

### After this change

Run: `/private/tmp/flora-round56-dsv4flash-nested-recursion-guidance`

The model actually used nested delegation. Root spawned L2, waited up to 300
seconds, read its complete result, and reviewed the real digest. L2 spawned and
reported a nested L3 stage; L3's own contract fields did not pass, so the chain
returned `blocked` with `answer=null` and explicitly recorded that L4/L5 had no
observed evidence. No child was accepted by coercion. The run completed in
about 442 seconds with 3 model calls and 9 tool calls.

This is a meaningful behavior change, but it is not a five-layer success claim:
the remaining failure is now inside model-authored nested contract/review
execution rather than a host prohibition.

## Remaining limitations

- L3/L4/L5 contract evolution and budget use remain unreliable for this model
  and prompt; no factual or `claims_verified` guarantee is implied.
- Cross-model nested behavior after this change remains unverified; GLM has
  separate program-generation failures.
- The host still relies on explicit child contracts and review evidence; it
  does not infer a correct decomposition or silently repair a violating child.
