# Engineering round 12 — bounded collaboration and protocol boundaries

## Scope

This round starts from `835da0024bffd8b97648e6e124662b39ae25f1d6` and addresses
three observed reliability failures without changing the meaning of a tool result
or weakening consumer checks:

* one extra closing delimiter in a complete compiler bundle could prevent any
  action; a uniquely identifiable delimiter normalization now runs only when the
  repaired value is a complete bundle envelope, after which the normal frontend,
  IR, tool schema and runtime checks still run;
* a model-authored batch of independent workers required seven separate compiler
  turns; schema-4 now exposes `spawn_agents`, which accepts model-authored task
  specifications while retaining per-worker context, dependencies, review and
  completion gates;
* workers had no recursive delegation path. Coordinator workers can now create
  bounded read-only child Coordinators under an explicit `max_depth`, with
  independent durable directories and completion guards. A shared in-process
  `max_total_children` bound prevents accidental exponential fan-out. Nested
  workers are instructed to follow their assigned scope rather than copying the
  parent's worker count.

The `wait_agents` boundary also accepts the identity envelope returned by spawn
operations and consumes only its `agent_id`. Other result fields remain untrusted.

These changes preserve the two design requirements. A worker action changes task
state and yields an observation that determines the next collection/review phase.
Each handoff retains the original task authority, local assignment, evidence
references, dependencies and review disposition; a completed claim is not treated
as semantic proof. No host code chooses the calculus decomposition or answer.

## Real API evidence

The replacement credential worked for the supplied endpoint. The original key
returned HTTP 401 for minimal chat requests; it was not used as task evidence.

### Seven-worker calculus task, `deepseek-v4-flash`

* Baseline behavior: two compiler calls, zero tool calls, and no workers. The
  model's large batch program was rejected for malformed JSON.
* After bounded syntax normalization and batch coordination: 179.0 seconds,
  7 model calls, 13 tool calls, seven workers created, all seven results fully
  collected and reviewed, and the durable completion gate required an explicit
  work update before final return.
* The task is **not a semantic pass**. Two workers produced the same calculus
  problem. Parent review preserved `claims_verified=false` and relied on model
  reasoning over worker text; no independent source-to-answer oracle existed.
  The result is therefore evidence that collaboration and completion gates ran,
  not evidence of broad mathematical reliability.

### Nested task, `deepseek-v4-flash`, configured depth 2

The task requested two first-level workers, each required to create one child.
The runtime created nested workers and completed in 377.7 seconds with 13 parent
tool calls and eight parent model calls. The identity-envelope normalization
allowed the parent to wait on the actual child IDs.

This is also **not a semantic pass**. The model created more nested workers than
the requested one-per-parent shape, and one first-level worker reported `114`
instead of its assigned `42`; the parent recorded the discrepancy and returned
the arithmetic total using its own expected value. The run demonstrates bounded
recursive execution and review plumbing, while exposing the remaining need for
host-checkable cardinality and value-preservation contracts.

## Offline validation

Focused collaboration, frontend, recovery and protocol tests passed: 59 tests.
Ruff and `compileall` passed. A prior complete discovery run on this checkout
reached 432 tests; after reverting profile-only experimental fields, the only
environment error was the pre-existing `python-docx` dependency required by
`test_effect_finalization`, and the profile identity failure disappeared. The
full suite should be rerun in an environment with that dependency before release.

## Remaining work

Nested depth and total-child bounds are currently configuration controls; they do
not prove that a model respected a requested exact worker cardinality. Semantic
worker answers, type preservation and evidence-grounded checks remain open. The
next round should add model-independent task contracts for those obligations and
run paired DeepSeek/GLM tasks, while avoiding a growing collection of model-format
patches.
