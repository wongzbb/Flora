# Engineering Round 77 — Contract-preserving task completion boundary

## Scope

Round 76 showed that nested children and reviews could complete, while the model still failed when trying to rewrite the host-created required work goal through `update_work`. This round adds `complete_task(note, evidence, expected_revision)` only when the durable task-completion contract is enabled.

The operation preserves the immutable host-created `task` goal, checks that no other required work is pending/running/blocked, checks current required child-review readiness, validates evidence references, and then records the completion note. It does not infer decomposition, prove factual truth, accept child claims, or bypass the normal completion gate. The model can therefore turn a real completion observation into durable state without rewriting the contract.

## Offline verification

- Task completion, collaboration, compiler advisory, projected observe, and recovery suites: **101 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

Tests cover immutable goal preservation, rejection of unresolved required substeps, and the existing child review gate.

## Real API validation

Endpoint: configured Flora API (`/v1`), model `glm-5`.

Task: explicit root → L2 → L3 contracts, bounded `collect_completed_agent`, digest-based `review_agent`, and durable completion through `read_work` followed by `complete_task`.

Observed result:

- The first program attempted to pass a full spawn identity envelope where the tool schema required the opaque `agent_id`; the host rejected that request without replaying the spawn effect.
- The model repaired the phase and successfully executed `collect_completed_agent`, obtaining the L2 `child_value` containing the L3 result `{layer:3, answer:4}`.
- It called `review_agent` with the observed digest and received accepted/pass, then read the current work revision and called `complete_task` without changing the required goal.
- The final result was completed with `{layer:2, child:{layer:3, answer:4}, answer:4}` and `completion_checks.ready=true`; `claims_verified` remained false, correctly reflecting that review checks collection/contract integrity rather than arithmetic truth.
- 5 tool calls and 4 model calls were used; no side effect was replayed.

Cross-output comparison: Round 76 reached the same nested value but failed at the completion continuation; Round 77 reaches durable completion after the generic bookkeeping boundary. Earlier DeepSeek five-level chains also passed explicit contract/review checks. This is strong evidence for the specific collection/review/completion path, not a broad guarantee for arbitrary tasks or models.

## Limitations and next focus

The task used an explicit contract and simple arithmetic; complex semantic tasks, multi-worker fanout under load, deeper-than-three nesting, and other model output styles still need independent real API validation. The completion boundary intentionally refuses unresolved required work and does not mark claims as factually verified.
