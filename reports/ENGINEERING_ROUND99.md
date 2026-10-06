# Engineering Round 99 — bounded representation fallback and durable progress observations

## Scope

This round addresses two generic failure modes observed in real multi-agent runs:

1. A semantic-first compiler could receive an ambiguous semantic envelope (for
   example duplicate JSON keys). Retrying the same semantic representation was
   unsafe and often ended in `needs_program` before any child was started.
2. After a large child collection or a completion rejection, context/checkpoint
   retention could remove the only compact view of current child/workflow state.
   The model then had to rediscover settled state, sometimes repeating collection
   or spending the parent budget on malformed synthesis programs.

The changes preserve the dual-control boundary. A tool action still advances the
world/task state and yields an observation; the observation is exposed to the
next phase. The host never merges duplicate fields, coerces output types, or
marks a task complete. Child contracts remain assume–guarantee interfaces and
completion remains an observed readiness condition, not a truth oracle.

## Changes

- Added one bounded semantic-to-low-level representation switch for semantic
  validation failures that indicate an ambiguous/unusable envelope. The switch
  keeps the same task, anchor and tool capabilities, emits an audit event, and
  cannot oscillate back. Duplicate keys are rejected; they are never guessed or
  merged. Other semantic/task failures still follow normal bounded repair or
  rejection paths.
- Persisted a bounded `__openharness_progress_observation__` projection after a
  workflow phase has executed. It records the current epoch, trace digest and
  host completion/child readiness observation with `claims_verified=false`.
  This projection is retained across context compaction and is explicitly
  observational; it does not satisfy completion on its own.
- Persisted a bounded child `completion_observation.json` as soon as the host
  rejects a contracted return. If a worker then stalls before producing a
  terminal result file, the parent can still read the rejection and revise the
  local contract rather than treating the child as an unexplained absence.
  `read_agent` and result projections expose that observation without exposing
  raw internal traces.

## Offline verification

- Full discovery: **548 tests passed**, no skips.
- Targeted structured-planner, collaboration, task-completion, handoff and
  runtime invariant tests passed.
- `compileall` and `git diff --check` passed.
- New deterministic tests cover duplicate semantic-envelope fallback, durable
  completion observations at a child stall boundary, and host progress
  observations surviving a workflow phase.

## Real API verification

Runs used the user-authorized endpoint and were kept outside the repository.
Credentials are not recorded here. The task was the same seven-child calculus
shape: each child had a contract requiring nonempty `question`/`solution`/
`answer` strings; the parent collected, reviewed and summarized all seven.

### DeepSeek

Directory: `/private/tmp/flora-round102-deepseek-7calc-progress-20261006a`

- Final status: `completed`.
- Seven children were spawned, collected and accepted (`contract_status=pass` for
  each); 5 tool calls and 6 model calls; about 154 seconds.
- The final result reports `claims_verified=false`; this is a contract/collection
  result and does not prove the mathematical truth of generated solutions.

### GLM-5.3

Directory: `/private/tmp/flora-round102-glm53-7calc-progress-20261006a`

- Final status: `completed`.
- Seven children were spawned, collected and accepted (`contract_status=pass` for
  each); 4 tool calls and 11 model calls; about 138 seconds.
- The run encountered type/argument faults while constructing intermediate
  programs, then used the observed faults to continue. No effect was replayed.
  The final result reports `claims_verified=false`, so mathematical correctness
  remains outside the host guarantee.

For comparison, a pre-progress-observation GLM run in this round completed all
child reviews but exhausted the parent budget while repeatedly reconstructing the
final phase. That failure is retained as a limitation/evidence point rather than
hidden by changing the test budget.

## Remaining limits

- A bounded representation switch cannot repair arbitrary semantic planning or
  prove a generated answer. Repeated pure type/shape faults can still end in a
  budget or compiler failure if the model does not use the observations.
- The two successful live runs cover one seven-child task shape. They do not
  establish broad reliability for arbitrary multi-tool or more-than-three-level
  nesting; those require additional cross-model holdout runs.
- Completion and contract checks establish observed interfaces, dependencies and
  evidence state. They intentionally do not certify mathematical truth.
