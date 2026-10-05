# Engineering Round 92 — retain phase handoffs during context omission

Date: 2026-10-05  
Branch: `general-agent`  
Base: `1edfb93`

## Change

The compiler's bounded context view no longer drops the entire `memory` object
when the prompt exceeds `max_context_bytes`. It keeps the user input under
`memory.data` and host phase-control observations under
`memory.__openharness_*` (including continuation and completion observations),
while omitting unrelated memory. Each retained value is checked against a
separate bound; an oversized value is represented by an explicit digest marker
with `recover_with: read_memory`. The full memory digest and retained-key list
remain in visibility metadata, so omission is observable rather than silently
treated as an empty state.

This is a generic phase-handoff rule. It does not infer a task decomposition,
rewrite a contract, coerce a value, or make an omitted observation available as
if it were complete. The authoritative trace and memory remain unchanged; only
the compiler delivery view is projected.

## Offline verification

- Consumer-recovery, completion, structured-planner, recovery and phased
  compilation tests: **75 passed** in 1.22 s.
- Handoff authority, consumer recovery, kernel invariants, long-task and
  collaboration/coordinator regression: **115 passed** in 216.21 s.
- The new invariant confirms that context omission retains exact user data and
  continuation state while excluding unrelated large memory.

## Real API verification status

The supplied gateway remained unavailable throughout this round: direct
`/v1/models` probes received immediate TCP connection refusal. The previous
round's real GLM and DeepSeek traces remain recorded in
`ENGINEERING_ROUND91.md`; no new live task is claimed for this context-view
change. This checkpoint is pushed to preserve the work, but it is not a broad
reliability result. A live API task and cross-output comparison are still
required before counting this mechanism round as validated.

## Limits and next work

Retaining phase-control state reduces one source of semantic recovery failure,
but a model may still choose an invalid consumer, emit an overlong plan, or
mis-handle a contract observation. If the retained value itself is too large,
the model must use the actual receipt or memory read boundary; the digest is not
the value. After the gateway returns, rerun the completion-retry task and then
the seven-child, five-worker multi-tool and deeper nested tasks under at least
two output behaviors.
