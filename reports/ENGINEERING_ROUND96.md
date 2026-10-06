# Engineering Round 96 — phase handoffs and nested lifecycle closure

## Scope

This round consolidates the pending reliability work after Round 95.  It does
not add a calculus-specific planner or a model-specific answer path.  The
changes make the host own phase boundaries and durable child state while
keeping task decomposition, local contracts and semantic choices model-authored.

## Changes

- A semantic action phase now receives explicit guidance to perform at most one
  effect before returning a replan with a short state.  Spawning, collection,
  review and final aggregation are separate observation points.  The correction
  prompt has the same restriction and forbids prose completion claims in an
  intermediate return.  This makes the actual effect receipt the input to the
  next phase instead of asking one fragile response to encode the entire
  collaboration protocol.
- The semantic boundary accepts one JSON-equivalent Python literal only when
  its values, keys, duplicate-key policy, depth and node count fit the same
  bounded JSON value rules.  Python comments, names, adjacent string
  concatenation, tuples, sets, calls and trailing syntax are rejected.  This is
  a transport representation boundary; it does not coerce task values or
  bypass lowering, tool checks, contracts or completion gates.
- A single-step semantic object is normalized to a one-item step list in the
  boundary layer, including known nested plan positions.  Literal payloads are
  copied as data and are not recursively interpreted as plans.
- Queued children without a dispatched future are durably settled as `paused`
  during pause/close.  Coordinator dispatch retains its existing per-future
  error handling.  A terminal coordinator result closes a nested scheduler
  before taking its `nested_completion` snapshot, so a parent cannot publish a
  stale `running` descendant after the child scheduler has been stopped.
- Completion projections expose host-derived child status, result availability,
  digest, contract observation, nested completion and failure separately from
  model-returned values.  `claims_verified` remains false; a digest or review
  label is not presented as proof of semantic correctness.

These mechanisms preserve the two required ideas.  An effect advances the
task and yields an actual receipt; that receipt selects the next phase or a
contract/replacement decision.  A child handoff carries its declared output
shape and host review state, and a violated shape is rejected and can be
reassigned only through an explicit replacement contract.

## Offline verification

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q`: **543
  passed**.
- Focused structured-planner, collaboration and coordinator-recovery tests:
  **103 passed** in the final working tree.
- `PYTHONPATH=src .venv/bin/python -m compileall -q src`: passed.
- `git diff --check`: passed.

The new tests cover bounded Python-literal compatibility and rejection of
Python-only comments/string concatenation, one-step shape normalization,
host-derived worker observations, and durable pausing of queued children.

## Real API verification

All runs used the user-authorized OpenAI-compatible endpoint and the same
7-calculus-child task.  Credentials are intentionally absent from this report.
The temporary run directories are outside the repository.

### DeepSeek Flash, parent budget 30

- Run: `/private/tmp/flora-round97-deepseek-calculus7-lifecycle-20261006a`.
- Final status: `completed`; completion gate `ready=true`.
- Seven current child IDs; each result was collected and accepted.
- Parent usage: 7 model calls, 5 tool calls, 165.8 seconds.

### GLM 5.3, parent budget 30, before phase guidance

- Run: `/private/tmp/flora-round97-glm53-calculus7-lifecycle-20261006a`.
- Final status: `needs_program`; no child was admitted.  Three semantic
  responses were rejected for malformed/structurally incompatible plans.
- This is retained as a counterexample: the endpoint was reachable, but the
  model output was not a valid executable phase.

### GLM 5.3, parent budget 30, after phase guidance

- Run: `/private/tmp/flora-round98-glm53-calculus7-phase-20261006a`.
- Final status: `completed`; completion gate `ready=true`.
- Eight registry rows existed because one original child was rejected by its
  output contract and explicitly replaced; seven current children remained.
  All seven current results were complete and accepted.  The old rejected row
  remains historical and is not counted as a successful child.
- Parent usage: 21 model calls, 12 tool calls, 305.3 seconds; two calls had
  unknown provider usage and were charged conservatively.

An earlier GLM run with an intentionally tighter 12-call parent cap reached
seven current children but exhausted the parent before reviewing the
replacement; it is a failure, not a success claim.  The successful runs show
that the phase protocol can recover across two output styles, but do not prove
arbitrary-task reliability.

## Limits and next work

- The calculus task checked child count, collection, contract shape and review;
  it did not mechanically verify the mathematical truth of every generated
  question.
- The GLM success is one paired run after a preceding failure.  More repeats,
  holdout tasks and output-style variation are required before claiming broad
  reliability.
- A fresh real nested chain deeper than three levels has not yet been rerun
  after the close-before-snapshot change.  The host invariant is covered
  offline for queued work, but deep runtime evidence remains pending.
- Host projections intentionally leave `claims_verified=false`; semantic
  correctness still requires task-specific source/output evidence and cannot be
  inferred from JSON validity or accepted review alone.
