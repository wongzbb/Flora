# Engineering Round 94 — replacement lineage invariants

## Scope

This round hardens the host side of semantic child replacement. A rejected
child remains an audit record, while a replacement must carry forward the
obligations that still apply. The change is deliberately independent of model
JSON syntax and does not infer a task answer.

## Changes

- Ordinary handoff identities no longer include a null `replaces` field. This
  preserves recovery deduplication for records created before replacement
  lineage was introduced.
- Admission rechecks the target's current result and state digests while the
  replacement is committed. A stale review cannot authorize a new child.
- An unknown side-effect outcome cannot be replaced automatically.
- A required child cannot be replaced by an optional child. A target with a
  contract must receive a replacement contract, and a target with a positive
  nested-child lower bound cannot silently lose that bound.
- The replacement receives a bounded, explicitly unverified observation of the
  prior disposition, contract check, result digest and state digest. The prior
  row remains immutable for review and audit; reviewing or resuming it after
  supersession is rejected.

These checks preserve dual control: the observed rejection changes the next
admission and is passed to the replacement planner. They preserve
assume–guarantee composition: replacement cannot erase a required interface or
nested obligation merely by changing names or optionality.

## Offline verification

- `tests/test_collaboration.py`: **69 passed**
- `tests/test_coordinator_recovery.py tests/test_task_completion.py tests/test_kernel_invariants.py`: **36 passed**
- `python -m compileall -q src`: passed

New regression cases cover requiredness/contract preservation, historical
superseded rows, and ordinary handoff signature compatibility.

## Real API verification

The same user task was run against the supplied OpenAI-compatible endpoint with
the repository's live authority profile and the two requested model families.

### DeepSeek Flash

- Task: five distinct subagents, each asked to use 1–3 tools.
- Result: `completed`, host completion gate ready.
- Parent budget: 10 tools, 3 model calls, 223.8 seconds.
- Four children were accepted. One child was blocked because its requested
  tools were not granted; the model then admitted a replacement with a revised
  contract. The replacement was collected and accepted. The old blocked row is
  present in `superseded_workers` and retains its rejected/blocked review.
- This is evidence that replacement lineage executes naturally for this run; it
  is not evidence of broad task correctness or factual truth.

### GLM 5.3

- Same task and budget profile.
- Result: **`needs_program`**, not a success. A child exhausted its model-call
  budget; other children returned contract-shape violations or repeated
  semantic-plan JSON validation failures. The parent attempted bounded
  replacement and collection, but two required replacements remained
  unaccepted when the parent stopped.
- Parent budget: 13 tools, 8 model calls, 472.2 seconds; four original rows
  were superseded. No child answer was fabricated and the completion gate stayed
  false.

The model difference confirms that the lineage fix is live, while semantic-plan
generation and budget efficiency remain unresolved robustness work. In
particular, this round does not claim that `needs_program` has been solved.

## Limits and next work

The host still cannot prove the factual truth of arbitrary child answers, and a
root model can report a malformed free-form summary even when the child review
chain is sound. The next round should improve the model-independent final
delegation observation/projection and reduce repeated semantic compilation
without weakening contracts or turning syntax repair into task-specific logic.
