# Engineering Round 89 — observed review projections and bounded semantic recovery

Date: 2026-10-05  
Branch: `general-agent`  
Base: `77025f5`

## Change

Successful `review_agent` and `review_agents` observations now carry bounded
`result_available`, `child_status` and `child_value` projections alongside the
review disposition, contract status and result digest. These are explicitly
unverified observations. Values over 32 KiB are omitted with a digest marker,
so review does not duplicate an unbounded child result; the parent can use its
previous complete collection when needed. This removes an unnecessary semantic
join between collection and review while preserving the separate acceptance
gate and evidence checks.

The semantic response boundary now permits one mechanically identified missing
comma. The decoder position must make the insertion unambiguous, the candidate
must parse as one strict object, and all normal semantic lowering, anchor,
effect, contract and completion validation still runs. No key, value, result or
control flow is inferred by the repair.

## Verification

- Structured compiler, collaboration, coordinator recovery and frontend
  regression: **125 passed** in 7.07 s.
- Real GLM 5.3 simple task completed after this change (round 113).
- Real DeepSeek Flash simple task completed after this change (round 114),
  with a runtime observation and bounded recovery before the final result.
  This is a cross-model smoke comparison, not a reliability estimate.
- Real GLM 5.3 three-child calculus task successfully spawned and fully
  collected all three children, then failed before review because the next
  semantic response was malformed JSON after bounded repairs. The failure is
  retained as negative evidence; the new review projection was not reached in
  that run.

## Limits and remaining work

The seven-child and three-child tasks still show a broader issue: after a valid
observation, the model can spend its semantic repair budget on an overlong or
malformed next program. A single boundary comma repair cannot make arbitrary
JSON reliable and is intentionally not used to infer semantics. Nested
multi-agent execution, the five-worker multi-tool task and factual correctness
of child answers remain unverified. The next round should reduce the size and
cross-step state required for semantic replanning, while keeping observations,
contracts and completion gates explicit.
