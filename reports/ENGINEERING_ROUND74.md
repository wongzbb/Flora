# Engineering Round 74 — Complete bundle-envelope repair guidance

## Scope

Round 73 showed a model returning a `revisions`-only object after a nested-task prompt, so no action could execute. This round adds a compiler repair hint for that generic top-level protocol error: a repaired bundle must include `programs`, `incumbent`, `diagnostics`, `expected_epoch`, and `expected_digest`; `revisions` remains optional. The hint does not synthesize any program, infer a task decomposition, relax validation, or change execution conclusions.

## Offline verification

- Collaboration, compiler advisory, projected observe, and recovery suites: **89 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

The new regression feeds a revisions-only invalid bundle followed by a valid bundle and checks that the repair request preserves the complete protocol envelope requirement.

## Real API validation

Endpoint: configured Flora API (`/v1`), model `glm-5`, the same explicit three-level root → L2 → L3 contract task used in prior rounds.

The API run reached the following observed phases:

- The first revisions-only response was repaired sufficiently to install a program and execute `spawn_agent` and `wait_agents`; this is progress beyond Round 73's zero-tool failure.
- The next generated phase attempted `collect_agent`, but passed `result_available` to a continuation block whose declared parameter list contained only `agent_id`. Strict validation rejected it: `after_wait.term: argument keys must match 'collect_l2' parameters; expected ['agent_id'], got ['agent_id', 'result_available']`.
- The process handle disappeared after the tool-session interruption, but the persisted event log ends at that compiler rejection, with no final return or accepted three-level result. No effect replay was observed.

Cross-output comparison: Round 72's GLM-5 two-level task completed with the stable review summary; Round 73's three-level task failed before any tool call due to a revisions-only bundle; Round 74 reached real delegation but still failed at exact continuation arguments. The evidence supports a narrower improvement in bundle-envelope recovery, not broad nested reliability.

## Limitations and next focus

Exact block-parameter generation remains a model-facing failure even with collect/review guidance. The runtime continues to reject extra arguments deliberately, because silently dropping them could hide a mismatched contract. Future work should improve a generic phase handoff or continuation construction without weakening parameter semantics, then validate again on both GLM-5 and a different model/output style.
