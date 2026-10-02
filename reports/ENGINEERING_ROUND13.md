# Engineering round 13 — explicit assume–guarantee handoffs

## Scope

This round adds a bounded, model-independent contract field to `spawn_agent`
and `spawn_agents` contexts. A handoff may now carry `assumptions`, typed
`inputs`, typed `outputs`, `guarantees`, `dependencies`, and
`evidence_requirements`. The host validates the envelope and preserves it in
the worker's durable handoff; it does not claim that a worker's answer proves
the guarantee. Worker instructions require a violated assumption or evidence
requirement to be reported instead of silently changing a value type.

The contract is deliberately a semantic boundary rather than a model-format
repair. JSON remains the transport representation, while the host keeps the
original contract and evidence for later review. The existing dual-control
scheduler still makes an action both advance task state and produce an
observation: after `spawn_agents`, the model observed the returned identities,
then waited, read complete result windows and reviewed each result before it
could finish. No additional diagnostic was inserted when that observation was
already sufficient.

## Real API validation

The replacement user credential and endpoint were used. A direct request first
confirmed the endpoint was reachable after the local sandbox rejected an
unprivileged connection. The original credential remains unauthorized.

### DeepSeek Flash

Task: two workers compute `17+25` and `9*8`; each must return a numeric result
and derivation; the parent must read and review both complete results and keep
numbers numeric.

* 101.6 seconds, 5 model calls, 8 tool calls, 2 workers;
* both worker handoffs contained generated contracts, including numeric output
  guarantees and evidence requirements;
* both complete worker results were read and reviewed, and the completion gate
  required a durable work update;
* final value was `{"sum_two": 42, "mul_two": 72, "total": 114}` with JSON
  numbers.

This is a mechanism pass, not a correctness proof: `claims_verified` remained
`false`, and the host did not independently recompute the arithmetic or check
that every guarantee was true.

### GLM-5 comparison

The same task with `glm-5` made two model calls but produced no tool effects.
Both compiler attempts were rejected for an undefined variable (`agent0`), so
no worker or contract was installed. This paired result shows that the new
contract is not a model-specific success claim and that cross-model program
generation remains a reliability limitation.

## Offline validation

`tests.test_collaboration`, `tests.test_frontend` and `tests.test_recovery`
passed: 60 tests. Ruff, `compileall`, and `git diff --check` passed. New tests
cover contract preservation through a handoff and rejection of a malformed
guarantee list before worker dispatch.

## Remaining limitations

The host currently validates contract shape and carries it through the
handoff, but does not synthesize a general semantic checker for arbitrary
claims, exact worker cardinality, or numeric correctness. A parent can still
return an unverified value after reviewing worker text; the durable state keeps
that distinction explicit. Nested depth and total-child limits remain bounded
configuration controls. Future work should connect contracts to observed
inputs, output types, evidence and revisable decomposition, while preserving
format-independent semantics and testing paired models.
