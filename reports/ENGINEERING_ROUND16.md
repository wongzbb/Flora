# Engineering round 16 — nested delegation contracts

## Scope

Handoffs may now declare bounded nested cardinality with
`contract.delegation.min_children` and `max_children`. A nested coordinator
uses those bounds as both a per-task child quota and a completion condition. A
minimum that exceeds the configured depth or child quota is rejected at
handoff time. When a worker has nested work, its durable result carries the
nested completion snapshot; `review_agent(accepted)` refuses a result whose
nested contract is still incomplete.

This is a semantic contract check, not a task-specific worker-count rule. The
model still chooses whether a separable nested subtask is needed and supplies
the contract. The host only enforces the declared local interface. The
dual-control loop is preserved: child status and nested completion are actual
observations that can force rejection, revision or a blocked result.

## Real API validation

### Four-level nested task, `deepseek-v4-flash`

The task requested one child at each of four levels. The runtime created the
nested coordinator tree and recorded the declared one-child bounds. An
intermediate worker exhausted its configured budget, leaving its nested
completion `ready=false`. The parent could not accept that result; it recorded
the budget limitation and the run ended without a verified leaf answer. This
is a **negative validation** of the new gate: the root did not silently treat
the arithmetic inference as a leaf observation. `claims_verified` remained
false.

### Ordinary collaboration regression, `deepseek-v4-flash`

The same round's non-recursive task asked two workers to compute `13+29` and
`6*7`. It completed in 96.3 seconds with 4 model calls and 8 tool calls. Both
results were fully read and reviewed, the work completion gate passed, and the
parent returned `42`, `42`, and `84`. The gate did not regress ordinary
multi-agent work; the result remains unverified by design.

## Offline validation

The collaboration, frontend and recovery suites passed: 67 tests. Ruff,
`compileall`, and `git diff --check` passed. Tests cover declared child bounds,
quota enforcement, invalid contracts, and refusal to accept a worker with an
unfinished nested coordinator.

The full discovery run on the latest pushed commit reached 438 tests with one
environment error: `test_effect_finalization` requires the unavailable
`python-docx` package. This is separate from the changed code.

## Remaining limitations

Nested contracts are opt-in and models do not yet generate them consistently.
The negative four-level run also shows that nested budgets can be too small for
the generated repair/review program; a bounded failure is preferable to an
unverified success, but budget allocation and resume strategy still need work.
The host does not prove arithmetic truth, evidence meaning or complete task
coverage. Cross-model program-generation failures remain open.
