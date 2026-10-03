# Engineering Round 71 — Generic complete child collection boundary

## Scope

This round adds `collect_agent(agent_id, limit)` to the collaboration boundary. It mechanically reads every bounded page of one child result, assembles and parses the complete observed result, and preserves the existing read-window and digest bookkeeping used by `review_agent`. It never waits, accepts, reviews, coerces, or retries a child. A pending child remains unavailable, so the parent must choose a new wait/collect action or record a limitation.

The purpose is a model-independent phase boundary for the dual-control design. Collection is an observation-producing action; the parent still has to use that observation to choose review, blocking, replacement, or a later pure consumer. The child contract remains an assume–guarantee interface, and review still checks the declared output guarantee and delegation bounds. Guidance now recommends one raw result parameter for each collect outcome target, with pure field extraction inside the branch; it does not loosen exact continuation arguments.

## Offline verification

- Collaboration, compiler advisory, projected observe, and recovery regression suites: **88 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

The new regression starts a child, collects it with a one-character page limit, verifies complete assembly and the original digest, then calls `review_agent`. The review gate remains required.

## Real API validation and cross-output comparison

Endpoint: configured Flora API (`/v1`), model `glm-5`, the same explicit root → L2 → L3 contract task used for the previous repair round.

Previous Round 70 (manual `read_agent` pagination): the root reached 14 epochs and 12 model calls, but repeatedly failed while constructing read/review continuations; no `collect_agent` action existed and the nested task did not complete.

Round 71b with `collect_agent` and minimal target guidance:

- 16 epochs, 16 tool calls, and 12 model calls; no effect was replayed.
- The root successfully executed `spawn_agent`, `wait_agents`, `collect_agent`, and `review_agent`. The collection action returned a complete observed result and a digest that was passed to review. After the child was resumed, the same collect/review sequence was reached again.
- The task still failed. The nested child had its own review/program faults, and the root later attempted invalid pure reads of review/identity data. The run ended at the configured model-call limit with `claims_verified=false`; no completed three-level answer was accepted.

This is measurable interface progress, not a broad reliability claim. It shows that a generic collection primitive removes the pagination reconstruction failure seen in Round 70, while nested review result handling and local observation consumption remain unresolved. The strict contract/review gates correctly rejected unfinished work.

## Limitations and next focus

`collect_agent` does not solve model planning, nested child program validity, review response extraction, or budget exhaustion. The next improvement should make the review observation boundary equally explicit and stable for arbitrary nested depth, while retaining independent collection, digest matching, contract checks, and evidence-before-acceptance. Further validation must include at least one non-GLM output and a successful deep nested run before any broad reliability claim.
