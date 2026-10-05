# Engineering Round 86 — semantic-first compilation for complex profiles

Date: 2026-10-05  
Branch: `general-agent`  
Base: `3dc32f4`

## Finding

The five-worker GLM 5.3 trace showed that starting every phase in low-level
block-list IR wastes a compilation call on complex collaboration programs. In
one run the first low-level response failed on a `get` arity error; the semantic
fallback then consumed its remaining attempts on malformed JSON and an invalid
map binding. No effect ran.

## Change

`LLMCompiler` now accepts a boolean `semantic_first` strategy. When enabled,
the compiler starts with the bounded semantic action language and host-owned
continuations, while retaining the existing low-level path as an explicit
fallback. This changes only the representation handed to the model; it does
not select workers, answers, tools or contracts. The live authority profile
enables it for complex sessions. A missing validation error is represented as
an empty diagnostic rather than crashing the semantic prompt builder.

The host still enforces the same dual-control and contract boundaries. Effect
results remain the observations that determine the next phase, and contract
type violations, incomplete children and stale reviews remain blocking facts.

## Verification

- Semantic/compiler/recovery focused tests: **61 passed** in 1.30 s.
- The prior final-code offline suite remains **493 passed**; a full rerun after
  this configuration-only strategy is still pending because the prior full run
  took 6806.24 seconds.
- Real GLM 5.3 five-worker task with semantic-first enabled: **not completed**
  in 98.79 s. It did avoid the initial low-level `get` failure, spawned five
  workers, collected results and attempted review. Two child programs failed
  their own compilation, one result violated its declared output type, and the
  parent spent bounded replacement/review phases before its semantic plan
  attempts were exhausted. The completion gate stayed closed. This is negative
  evidence about long nested collaboration, not an API outage.
- A real GLM 5.3 semantic-first simple task (`2+2`) **completed** with numeric
  value `4` in 4.79 s. This verifies the configured path and completion
  bookkeeping, but is not evidence for broad multi-agent reliability.

## Limits and next step

Semantic-first reduces one representation transition but does not make a model
able to author an arbitrarily large recovery workflow. Child program failures
still consume child budgets, and parent replacement plans can grow too large.
The next improvement should make continuation state and replacement work
incremental and host-schedulable, so a model compiles one bounded recovery
phase instead of regenerating the whole future workflow. It must preserve the
actual failure observation and contract revision rather than accepting a child
by shape alone.
