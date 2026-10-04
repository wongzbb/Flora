# Engineering Round 80 — semantic action recovery and contract boundary robustness

Date: 2026-10-04 (Asia/Shanghai)
Branch: `general-agent`
Base checkpoint: `835da0024bffd8b97648e6e124662b39ae25f1d6`

## Why this round exists

Repeated `Status: needs_program` results were not a JSON formatting problem alone. The
model was being asked to construct low-level continuation signatures, scopes and
branches at the same time as it chose the task decomposition. A single missing
argument, unknown target, or runtime consumer mismatch could therefore discard an
otherwise useful phase. The round keeps low-level IR validation strict, but adds a
semantic action phase in which the host synthesizes the continuation plumbing.

## Changes

* `src/flora/language/compiler.py`
  * Classifies structural IR failures (unknown targets/resumes, argument and
    resume parameter mismatches, undefined locals, duplicate SSA bindings,
    malformed block/terminator shapes and unknown pure operations) as
    representation failures.
  * Switches to a bounded semantic action phase before ordinary repair exhaustion.
    Semantic validation has its own two-attempt correction budget and reports the
    concrete host error back to the planner. Tool capabilities, anchors and tool
    schemas remain strict and are not weakened.
  * Accepts one complete semantic JSON object at the response boundary when an
    OpenAI-compatible relay appends non-JSON out-of-band text. A second JSON
    candidate remains rejected; discarded trailing bytes are audited.
  * The semantic prompt explicitly requires a complete outer `{steps, return|replan}`
    object and permits omitted arguments for no-parameter calls.
* `src/flora/language/structured.py`
  * Adds a small semantic expression normalizer for variables, literals, pure
    operations, field projections and computed records.
  * Lowers mapped returns and map expressions into bounded accumulator loops,
    preserving observed values and index/value scope.
  * Allows save-only tool error handlers to preserve the actual error and replan,
    rather than rejecting the whole phase; explicit `on_error` plans remain
    supported.
  * Applies a 32-effect bound in addition to statement/block bounds. No effect is
    executed while lowering.
* `src/flora/general/coordinator.py`
  * Clarifies the generic contract authoring protocol with a nested object example
    and tells planners not to flatten unknown tool observations into guessed
    primitive fields. Contract review still rejects mismatches; it never coerces
    a receipt into compliance.
* `tests/test_structured_planner.py`
  * Covers projections, save-only error replanning, omitted no-argument fields,
    mapped returns, map assignments, nested records, relay trailing text and
    real effect-once execution.

## Verification

Offline:

* Targeted compiler/structured/recovery/compact tests: **63 passed**.
* Full discovery: **494 tests, 1 known environment-sensitive error**. The error is
  `test_evidence_coverage.EvidenceCoverageTests.test_oversize_table_rows_require_complete_real_source_and_reject_aggregate_source`;
  document parsing exceeded its existing 30-second deadline before this round's
  code was exercised. `compileall` and `git diff --check` passed.

Real API (user-supplied OpenAI-compatible gateway, keys omitted):

* GLM-5.3, exact task `调用7个子agent，每个子agent出一道微积分题目。`,
  `/private/tmp/flora-round100-glm53-exact-seven-20261004a`: completed in 234.3s
  with 7 distinct children, 18 tool calls and 7 model calls. All seven results
  were collected and reviewed; three matched their declared object contract and
  four returned raw strings against an object contract, so those four were
  explicitly blocked and preserved as limitations. No `needs_program`.
* DeepSeek-v4-flash, exact task `你能调用5个子agent，每个子agent调用1到3个工具做不同的是事情，具体做什么你来决定，请开始`,
  `/private/tmp/flora-round102-deepseek-v4flash-exact-five-tools-20261004a`: completed in 261.6s with 5 distinct children, 14 tool calls and 5 model calls. The first attempted batch was rejected because contracted children were optional; the next semantic phase changed the decomposition and all five children were collected/reviewed. Network-policy and unavailable-child-tool observations were reported as limitations; no `needs_program`.
* After the final bounded-loop correction, the same DeepSeek task was retried at
  `/private/tmp/flora-round103-deepseek-v4flash-exact-five-tools-20261004b` and
  completed in 175.9s with 5 distinct children, 9 tool calls and 2 model calls.
  An early completion proposal was rejected because the work ledger still had a
  required step; the model then read the work state and completed only after all
  child reviews were present. One child had zero calls because the requested
  artifact-status tool was unavailable, and web search was rejected by the
  network policy; both limitations were surfaced in the final report. No
  `needs_program` or unreviewed child remained.

Cross-model behavior is materially different but uses the same host mechanism: GLM
needed many small collection/review phases and exposed output-shape drift; DeepSeek
first exposed an invalid optional contract and then revised the handoff. In both
final runs the task reached the completion gate without replaying a successful
side effect. These are mechanism demonstrations, not proof of arbitrary task
correctness or factual verification.

## Design invariants and remaining limits

The dual-control path remains active: effect receipts and runtime faults are
observations that alter the next semantic phase; they are not silently converted
to values. Contract observations likewise branch the collaboration: accepted
children must pass the declared interface, while shape drift is retained as a
limitation or drives a revised handoff. The host supplies only generic bounds,
scopes, continuations, persistence and checks; it does not encode calculus or
worker-specific solutions.

Model-authored contracts can still be too narrow, as the GLM run shows. The current
round does not claim broad correctness, factual checking, stable transport under
all gateway conditions, or a fresh >3-level nested-agent API pass. Those remain
separate evaluation work.
