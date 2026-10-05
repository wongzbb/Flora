# Engineering Round 93 — semantic replacement lineage at the child boundary

Date: 2026-10-05
Branch: `general-agent`
Base: `ce36be8`

## Change

This round addresses a semantic completion failure exposed by a real GLM 5.3
run. A child result could be collected and rejected for violating its output
contract; the model could then start a fresh child with a revised contract, but
the host had no durable relationship between the two. The old required row
therefore continued to block completion even after the replacement was accepted.

`spawn_agent` and each `spawn_agents` item now accept an optional `replaces`
child ID. The host admits that relation only after the target has a durable
`blocked` or `rejected` review. Admission records `replaces` on the new row and
`superseded_by`/`superseded_at` on the old row atomically with the handoff. The
old result, contract observation and review remain readable for audit; it is
not retroactively accepted or deleted. The active logical child set excludes
the superseded row for completion, quota and nested-cardinality accounting, so
the replacement occupies the same obligation slot. A replacement must still be
collected and reviewed independently, and an unfinished or unknown-effect
child cannot be replaced through this path. Retrying an already admitted
replacement reuses its durable identity.

The batch review boundary also permits omitting `result_digest` after the host
has verified that the current result was fully collected. The host binds the
current digest and records `result_digest_source=host_current_result`; a caller
supplied stale or incorrect digest remains an error. This removes an unstable
cross-phase string copy without weakening the collection or review invariant.

These are generic state and evidence rules. They do not infer a calculus
decomposition, coerce a result shape, decide factual truth, or hard-code a
model's output syntax. A contract violation is still an observation that must
cause a pure-interface revision, an explicitly linked replacement, or a
blocked limitation. The dual-control loop is preserved: review observes the
failed interface, and that observation changes the next admitted program and
contract. The old and new assume–guarantee interfaces remain separately
checkable.

## Offline verification

- Full repository regression: **510 passed** in 344.66 s.
- Focused collaboration, coordinator recovery, completion, structured planner
  and consumer recovery regression: **136 passed** in 7.88 s; after the final
  test-only idempotent-retry assertion, the collaboration suite remained green
  at **66 passed** in 7.37 s.
- New invariants cover rejected-child blocking, explicit replacement lineage,
  independent replacement review, audit retention, early-replacement rejection,
  host-bound batch review digests and quota accounting.
- `git diff --check` passed.

## Real API verification

The supplied gateway was reachable with the provided credential during this
round. Both tasks below used the current uncommitted implementation, model
responses from the gateway, and the normal host completion gate.

1. **GLM 5.3, round 126:**
   `调用7个子agent，每个子agent出一道微积分题目，收集并分别审核7个结果后再汇总。`
   Seven children were admitted, collected in one bounded call and individually
   reviewed. The parent completed with `completion_checks.ready=true`; the run
   used 4 host tool calls and 6 model calls in about 86 seconds. No replacement
   was needed in this run, so it validates the ordinary multi-agent path rather
   than natural replacement selection.

2. **DeepSeek Flash, round 127, same task:**
   The model first read and revised its durable work plan, then used
   `spawn_agents → collect_completed_agents → review_agents`, updated the plan
   again and completed with `completion_checks.ready=true`. It used 10 host tool
   calls and 6 model calls in about 205 seconds, including one context/resource
   compaction. The final child gate had no pending, unreviewed, unaccepted or
   superseded workers.

Both runs show cross-model execution of the same collection/review/completion
semantics. They do **not** prove the mathematical answers: `claims_verified`
remained false, and the GLM run explicitly reported a child whose answer field
contradicted its own derivation. Contract review currently checks collection,
shape, evidence and lifecycle integrity, not domain truth.

The earlier GLM round 125 remains the motivating negative trace: replacement
children were accepted but the old rejected required IDs still blocked
completion because no lineage existed. This round fixes that host semantic gap;
the live runs above did not naturally exercise a replacement, so replacement
selection under a naturally observed violation remains an open evaluation.

## Limits and next work

The change prevents silent reassignment and removes a common completion deadlock,
but it does not decide whether a revised contract is substantively correct or
whether a model will notice and use `replaces`. A malformed program, provider
timeout, factual error, or unknown side effect still requires the existing
resume/blocking path. The seven-child and five-worker multi-tool paths now have
cross-model success evidence, but deeper nested delegation and a naturally
triggered contract-replacement task still need live evaluation. Keep reporting
those separately from transport failures, compiler failures, completion-gate
rejections and genuine task completion.
