# Engineering Round 91 — completion observations and effect result contracts

Date: 2026-10-05  
Branch: `general-agent`  
Base: `0a54ec1`

## Change

Completion-gate rejection now records the actual returned boundary value in
host memory under `__openharness_completion_observation__`, together with the
candidate, epoch and trace digest. The copy is bounded; when it is too large,
the host keeps an omission marker and a digest while the trace remains the
authoritative receipt. A subsequent semantic phase therefore receives a real
observation from the rejected return without referring to locals from the
discarded program or replaying an effect.

The semantic phase prompt now makes the phase boundary and effect contract
explicit: every effect call, including `complete_task`, saves its returned
envelope before the outer `return` or `replan`. The semantic normalizer rejects
a call without `save` with an actionable observation-preservation error. This
is a language invariant, not a provider-specific JSON rewrite; successful and
raised effect envelopes still follow the normal runtime branches.

## Offline verification

- Task-completion, structured-planner, collaboration, coordinator-recovery,
  recovery and frontend regression: **168 passed** in 7.76 s.
- The new invariants verify that a completion retry compiler sees the exact
  prior return, that oversized observations can remain digest-only, and that a
  missing `save` produces a bounded repair request containing the concrete
  semantic error.

## Real API evidence

- A real GLM 5.3 three-child run against the user-provided gateway (round 121,
  after the host completion-observation change) spawned three children,
  collected all three, reviewed each child and produced a parent summary. The
  completion gate correctly rejected the return because the host task ledger
  still had its required root step pending. The following semantic phase then
  failed because the model repeatedly emitted a bare `complete_task` call;
  no task completion is claimed. This is evidence that the host observation and
  completion gate were exercised, not evidence of broad task success.
- A real DeepSeek Flash simple task (`2+2`) completed in about 10 seconds in
  round 120 and remains a cross-model smoke check only.
- After these code changes, two full three-child retries (rounds 122–123) and
  direct `/v1/models` probes received TCP connection refusal from the supplied
  gateway with both supplied credentials. No effect was started in those
  retries, and this round therefore has no new live success claim for the
  prompt/error-guidance addition.

## Limits and next work

The new host observation removes one source of stale-local failures and the
semantic grammar now tells a repairing model exactly how to preserve effect
observations. It does not prove that a model will choose the right decomposition
or contracts, and the GLM multi-agent task still needs a successful retry after
the gateway returns. The seven-child calculus task, five-worker multi-tool task,
and deeper nested delegation remain required live evaluations. Results must be
reported separately for transport failure, compiler rejection, completion-gate
rejection and genuine task completion.
