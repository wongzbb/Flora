# Engineering round 17 — nested contract scope

## Scope

The delegation contract documentation now states its scope explicitly: bounds
apply to the current worker's direct children only. A leaf must declare
`min_children=0,max_children=0`; it must not copy an ancestor's bound. This
clarifies contract composition without adding task-specific planning logic.

## Real API validation

An explicitly scoped two-level DeepSeek task was rejected before effects when
the model emitted an ambiguous ordinary-effect term containing `resume` and
`error`. The frontend correctly refused to infer whether this meant an
ordinary effect or an observation. This is a negative boundary result.

The ordinary collaboration regression then completed successfully in 66.5
seconds with 5 model calls and 8 tool calls. Two workers computed `13+29` and
`6*7`; both were waited on, fully read and reviewed, and the parent returned
the two numeric results and total `84`. The durable work gate passed while
`claims_verified` remained false.

## Offline validation

The collaboration, frontend and recovery suites passed: 67 tests. Ruff,
`compileall`, and `git diff --check` passed.

## Remaining limitations

Models do not consistently emit scoped delegation contracts, and nested runs
can exhaust child budgets while compiling repair and review phases. The host
now blocks acceptance of incomplete nested work, but it cannot allocate a
correct budget, prove arithmetic truth, or infer the intended decomposition.
Ambiguous program variants remain rejected rather than guessed.
