# Engineering round 15 — bounded observe normalization

## Scope

One real DeepSeek run exposed a compiler boundary mismatch: a model emitted the
complete `observe` outcome shape (`success` and `error`) with `op: "effect"`
and no `resume`. The frontend now normalizes exactly that unambiguous field set
to `observe`; all existing observe signature, block-parameter, IR and runtime
checks still run. Partial or ambiguous effects are still rejected. This is a
single semantic boundary adapter, not a task-specific answer or a growing set
of model-format patches.

The change preserves dual control. In the real run, an invalid nested identity
envelope was rejected by the tool boundary; the model then used the observed
valid IDs and continued to read and review the actual workers. The action both
advanced collaboration state and supplied information that changed the next
program. Handoff contracts remained explicit and were checked against worker
output types.

## Real API validation

Task: two workers compute `17+25` and `9*8`, each with an assume–guarantee
contract requiring `result` to be a JSON number and `derivation` a string.

* completed in 209.2 seconds;
* 5 model calls and 11 tool calls;
* both workers were spawned, fully read to `next_offset=null`, and reviewed;
* both durable reviews recorded `contract_check.status = pass` for the declared
  output types;
* the parent updated all required work steps and returned `42`, `72`, and `114`.

The run still reports `claims_verified=false`. Arithmetic truth, exact
cardinality and evidence meaning are not inferred by the host. The observed
invalid nested envelope was rejected and recovered from; it was not silently
interpreted as a valid ID.

## Offline validation

The collaboration, frontend and recovery suites passed: 64 tests. Ruff,
`compileall`, and `git diff --check` passed. The new frontend test covers the
complete effect/observe envelope and confirms it lowers to ordinary IR.

## Remaining limitations

The frontend adapter only covers the exact unambiguous effect/observe shape.
Other model-generated program variants can still fail compilation, and the
host does not choose a task's decomposition or prove arbitrary guarantees.
Nested depth and exact worker cardinality remain configuration and model
responsibilities. Future work should address those semantic gaps while keeping
format handling at the boundary and retaining explicit observations and
revisable contracts.
