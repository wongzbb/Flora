# Engineering Round 37 — explain unavailable child observations

## Scope

The five-level live run in Round 36 stopped after a generated program tried to read a child result while that child was still running. The host already returned `result_available=false` and `result=null`; the VM reported only the generic `TYPE_ERROR`, leaving little semantic guidance for repair.

The VM now preserves that `TYPE_ERROR` boundary and adds a precise observation message when `get` is applied to `null`: the value is not a child answer, so the program must branch on `result_available/status` before dereferencing it. This is a diagnostic about the observed state, not a default value or an automatic success path. The existing identity-envelope diagnostic remains separate.

## Offline verification

- Relevant kernel, consumer-recovery, general, collaboration, handoff and coordinator tests: **99 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.
- Added a kernel invariant for the null-child observation message while retaining the original `TYPE_ERROR` code.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-flash`.

Run: `/private/tmp/flora-round37-dsv4flash-null-branch`

Task: a parent delegates to a child, the child delegates to a grandchild, and each layer waits, collects, reviews and verifies numeric `44-37 = 7`.

- Completed with value `7`, two nested child levels, 6 tool calls and 4 model calls in approximately 124 seconds.
- The result records `nested_delegation_children=1`, `review_disposition=accepted`, and a completed durable work step. No null child result was promoted as an answer.
- The model initially needed a compiler repair, then produced the valid phased program. The completion gate remained active until the work ledger was updated.

Cross-output comparison: Round 36's five-level run reached five nested identities but ended `needs_program` when a running child was dereferenced. This round's smaller nested run completed under the same host semantics; it is evidence that the diagnostic and bounded phase guidance are usable in at least one nested case, not proof of arbitrary-depth reliability.

## Limitations

- The new message has not yet been shown to recover the exact five-level failing program; that run failed before the new diagnostic was present.
- Five-level completion, fan-out of seven workers, mixed tool calls and GLM/default-model behavior remain unproven.
- `claims_verified=false` is correct for this arithmetic task because no external source claim was established.
