# Engineering Round 81 — semantic control-flow joins and contract completion gates

Date: 2026-10-04 (Asia/Shanghai)
Branch: `general-agent`
Base checkpoint: `835da0024bffd8b97648e6e124662b39ae25f1d6`

## Why this round exists

The previous round reduced low-level continuation failures, but real nested runs
still exposed two semantic risks. A required child with a rejected contract could
be counted as merely reviewed, allowing a parent to finish with an unfulfilled
interface. Separately, a model-authored structured plan that assigned the same
final value in both sides of an `if` could not use that value after the join: the
lowerer kept both branch locals out of scope and returned `needs_program` before
any useful action. This round moves both rules into the generic execution kernel.

## Changes

* `src/flora/general/coordinator.py`
  * Batch admission now validates quota and shared child budget before mutating
    existing records, persists the whole batch before dispatch, and rolls back
    the in-memory registry, directories and admission count on persistence
    failure. A dispatch failure remains a durable interrupted record instead of
    looking like an unadmitted retry.
  * Contracted dependencies now carry the producer contract and host review as
    explicit observations. The dependent worker is blocked unless the producer
    review is still tied to the current result/state digest and its checked
    evidence remains current. An explicit empty contract no longer bypasses that
    review boundary.
  * Required child completion now distinguishes `unreviewed_workers` from
    `unaccepted_workers`; `blocked` or `rejected` required children do not satisfy
    a nested contract. Optional children count toward only the upper cardinality
    bound, so they cannot satisfy a required minimum or make a zero-minimum
    contract permanently unfinished.
  * Nested tools are exposed only when the local handoff declares delegation
    bounds. A worker cannot silently create uncontracted grandchildren. At the
    depth limit, a zero-minimum delegation contract is vacuously reviewable;
    positive minima are still rejected at admission.
  * Evidence receipts now distinguish a complete offset-zero read from a full
    hash of a slice, treat free-text evidence requirements as `unknown`, and
    recheck only witnesses matched to declared structured requirements. Incidental
    reads remain audit observations without invalidating an unrelated contract.
  * Contract descriptors are recursively bounded by depth, node count, field
    count, union size and required-field count before execution.
* `src/flora/language/structured.py`
  * Structured `if` lowering now computes definite assignments on both paths.
    A name introduced on both branches is passed as an explicit SSA join value;
    one-sided branch locals and possibly-empty loop assignments remain scoped and
    fail closed. This is a language-level control-flow rule, independent of
    model, JSON layout or a particular diagnostic string.
* `src/flora/language/compiler.py`
  * A completion-gate rejection enters the semantic action phase directly, so
    the model receives the host observation as a new decision point instead of
    rebuilding low-level continuation IR just to repeat bookkeeping.
  * Pure IR arity diagnostics scoped to `.ops[...]` are treated as structural
    representation failures and routed through the same semantic phase.
* `src/flora/general/_document_process.py`
  * The macOS parser transport gives the initial bounded request enough time to
    drain into the worker pipe before using short memory-watchdog polling windows;
    this prevents a partial write from leaving the worker waiting for EOF until
    the 30-second deadline.

## Verification

Offline:

* Full discovery: **510 tests passed**.
* Focused semantic/compiler/coordinator/evidence suites: **130 tests passed** in
  the final targeted run.
* `python -m py_compile src/flora/general/coordinator.py` and `git diff --check`
  passed.

Real API (user-supplied OpenAI-compatible gateway; credentials omitted):

* DeepSeek-v4-flash, exact four-layer task, final run
  `/private/tmp/flora-round83-final-deepseek-nested4-20261004a`: 528.1s,
  4 tool calls and 5 model calls. The run reached L2/L3/L4 collection and host
  review. A nested output contract was rejected; the parent completion gate stayed
  `ready=false` and the run ended `needs_program` after bounded recovery. It did
  not claim the numeric result as verified.
* GLM-5.3, same exact four-layer task after the branch-join change,
  `/private/tmp/flora-round83-final2-glm53-nested4-20261004a`: 590.9s,
  8 tool calls and 6 model calls. L4 work was actually started, but both L2
  attempts eventually received `needs_program`; the root recorded blocked
  limitations and the host rejected `complete_task` because required child work
  remained unaccepted. There was no false successful completion.
* A preceding DeepSeek run before the branch-join change
  (`/private/tmp/flora-round83-deepseek-nested4-20261004a`) reached four layers and
  demonstrated the failure that motivated the completion gate: L3 was rejected,
  yet an L2 snapshot could otherwise look complete. The final code now makes that
  snapshot non-ready.

These runs are cross-model mechanism evidence, not a claim of broad task
correctness. Both models still show bounded compiler/recovery exhaustion on this
hard nested prompt; that remains an active reliability gap.

## Design invariants and remaining limits

The dual-control path remains explicit. Spawning, collection, review, source
rechecks and completion checks both change durable task state and produce host
observations. Those observations alter the next allowed action: a stale or
unaccepted producer blocks dependency dispatch, a rejected nested child keeps the
completion gate closed, and an IR/control-flow failure changes the compiler phase
rather than weakening a tool or contract check.

The assume–guarantee boundary remains compositional. Parent handoffs preserve
producer contracts, result digests, review dispositions and evidence states; a
contract violation is retained as a limitation or causes a fresh handoff, never a
string/number coercion or old-snapshot acceptance. The host owns generic scopes,
continuations, budgets, persistence, evidence and completion gates; the model
still chooses the semantic actions, decomposition and local contracts.

Not verified by this round:

* The host does not yet infer arbitrary consumer `inputs` compatibility from
  free-form contract prose or aggregate evidence from grandchildren into every
  parent contract; those remain explicit observations/model responsibilities.
* Default profiles still require enabling a positive `max_depth` for recursive
  delegation; the API runs explicitly used depth four.
* `claims_verified` remains false: review checks integrity, interfaces and
  receipts, not factual truth.
* Natural recovery after a rejected nested worker can still exhaust the bounded
  compiler/model budget. No small set of successful API runs is evidence of
  universal reliability.
