# Engineering Round 76 — Direct child value at the collection boundary

## Scope

Round 75 showed that a three-level GLM-5 chain could complete its child and review, but the parent still had to re-enter the full result profile to extract the child value. This round extends the generic collection view with host projections `child_status` and `child_value`, while retaining the full parsed `result`, pagination state, and `result_digest`. These projections are observations of the child result, not acceptance or truth claims. `collect_completed_agent` still waits only to a bounded timeout and still requires an independent `review_agent` call.

This keeps dual control: the wait/collection action advances child-state knowledge and produces an observation that determines whether the parent can review or block. The contract remains explicit and review-enforced; no output guarantee is relaxed.

## Offline verification

- Collaboration, compiler advisory, projected observe, and recovery suites: **90 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

Regression coverage checks both complete collection and the fact that collection alone leaves the review record unset.

## Real API validation

Endpoint: configured Flora API (`/v1`), model `glm-5`, explicit three-level root → L2 → L3 contracts.

Observed execution:

- `spawn_agent` followed by `collect_completed_agent` completed the L2 child, whose actual value contained the complete L3 value `{layer:3, answer:4}`.
- The parent called `review_agent` with the observed digest; the host returned accepted/pass. The parent then returned the complete L2 child value `{layer:2, child:{layer:3,answer:4}, answer:4}` as an observed result.
- The run still ended `needs_program` because a later model repair emitted a revisions-only bundle and the compiler rejected it for missing required envelope keys. No effect replay occurred and no child was accepted without review.

Cross-output comparison: Round 75 reached the same nested child completion but failed extracting the child from the full profile; Round 76's `child_value` boundary removed that failure and reached the correct nested value. The remaining failure is protocol repair/completion continuation, so this is not a claim that three-level GLM-5 tasks are fully reliable.

## Limitations and next focus

The model can still abandon a valid completed trajectory by emitting a revisions-only repair bundle after the completion gate reports remaining work. The next focus is preserving a valid completed candidate across completion-work bookkeeping and making required-goal updates consume exact observed goal text, without accepting an incomplete task or weakening the bundle protocol.
