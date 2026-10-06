# Engineering Round 98 — completion-boundary contract observations

## Scope

The previous rounds checked a contracted child mainly at parent collection and
review time.  A child could therefore return `completed` with a value that
violated its own local output interface; the parent discovered the mismatch
later, rejected the result, and often spent a replacement budget repairing a
mistake that the same child could have observed immediately.  This round moves
that host observation to the child's terminal return boundary.

The change is generic.  The model still supplies the local assume–guarantee
contract; the host only checks the declared output shape and machine-checkable
file/source receipts.  It never coerces values or decides whether a calculus
claim is mathematically true.

## Changes

- `Runtime` accepts an optional `return_validator` alongside the existing
  workflow completion guard.  A return is terminal only when both the task
  workflow and the observed value satisfy their respective checks.
- When a validator rejects a value, the runtime records the exact value (or a
  bounded digest), emits `completion_rejected`, and puts the task into a new
  semantic phase.  The next compiler call sees the observation in memory and
  can revise the program or contract without replaying settled effects or
  resetting budget.
- Validators may opt into a read-only snapshot of the current trace records.
  Coordinator workers use it to check declared `file_read`/`source_read`
  evidence without opening the active SQLite journal a second time.  Existing
  one-argument validators remain compatible.
- Contracted coordinator workers install a validator that checks their actual
  returned value and evidence before parent review.  Rejected output remains
  non-terminal and auditable; semantic contradictions still require parent
  review and may authorize a bounded replacement.  No model-specific JSON
  repair or value coercion was added.
- Checkpoint metadata records whether a return validator is required; restore
  refuses to continue without the original host callback, preserving the
  execution environment boundary.

## Offline verification

- Full discovery after the implementation and test expectation updates:
  **546 tests passed** (including the 72 collaboration tests); no skips were
  introduced.  The run includes the long-task, recovery, structured planner,
  nested collaboration and trace performance suites.
- `compileall` and `git diff --check` were run before commit.
- New deterministic coverage verifies that a primitive/object mismatch is
  rejected before terminal completion and that the next compiler phase sees
  the exact prior value.  A coordinator integration test verifies the same
  worker can return a corrected value after the rejection, with no replacement
  handoff.

## Real API verification

Runs used the user-authorized endpoint and were kept outside the repository.
The first sandboxed attempts were rejected by the execution environment before
network dispatch; the following two runs used the approved network execution
profile.  Credentials are omitted here.

### DeepSeek calculus-7 task

Directory: `/private/tmp/flora-round100-deepseek-calculus7-contract-20261006b`

- Final status: `completed`; `problem_count=7`.
- Seven original children completed, were collected, and were accepted with
  `contract_status=pass`; each result kept separate `question`, `answer` and
  `solution` fields.
- Parent usage: 10 model calls, 7 tool calls, about 186 seconds; no unknown
  usage or pending workers.

### GLM-5.3 calculus-7 task

Directory: `/private/tmp/flora-round100-glm53-calculus7-contract-20261006b`

- Final status: `completed`; seven current children were accepted with
  `contract_status=pass` and the final value retained seven independent
  structured results.
- Three initial workers produced internally contradictory answer/solution
  pairs.  The parent rejected those observations and admitted three explicit
  replacements through durable `replaces` lineage; the original rows and
  reviews remain in the audit log.
- Parent usage: 9 model calls, 7 tool calls, about 190 seconds; no unknown
  usage or pending workers.

These are two successful runs of one task shape, not a broad reliability proof.
The GLM replacements show that the new terminal contract gate does not replace
semantic review: shape/evidence checks pass while mathematical consistency can
still require a parent observation and bounded reassignment.

## Remaining limits

- A host validator cannot prove arbitrary truth, such as whether a generated
  calculus solution is mathematically correct; that remains an explicit parent
  review obligation.
- Provider interruption, budget exhaustion, malformed semantic plans and deep
  nested scheduling can still end in `needs_program` or `budget_exhausted`.
- Evidence requirements that are free-form text remain `unknown`; they do not
  silently become proof.  Use structured source/file receipts for automatic
  host checking.
- The two real runs cover DeepSeek and GLM on the seven-child shape only.
  Multi-tool children and more-than-three-level nesting still need repeated
  cross-model holdout runs before any broad reliability claim.
