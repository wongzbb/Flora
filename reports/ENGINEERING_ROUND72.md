# Engineering Round 72 — Stable host review summary

## Scope

Round 71 showed that a child could complete while its parent confused the review/identity envelopes. This round keeps the complete nested `review` record and adds stable top-level host observations to `review_agent`: `disposition`, `contract_status`, and `result_digest`. They are copied from the host's review decision, never from the child value. The tool description explicitly distinguishes these fields from untrusted child claims.

This preserves dual control: collection remains a separate observation-producing action, and the parent must still choose review and branch on the resulting host observation. It also preserves synthesizable contracts: review still enforces the declared output guarantee, evidence integrity, and delegation bounds; the summary only reduces envelope traversal.

## Offline verification

- Collaboration, compiler advisory, projected observe, and recovery suites: **88 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

The collaboration regression verifies that the full nested review remains present and that the top-level fields match the accepted review and actual result digest.

## Real API validation

Endpoint: configured Flora API (`/v1`), model `glm-5`.

Task: explicit two-level root → L2 contract, `wait_agents`, one `collect_agent`, digest-based `review_agent`, and a parent branch using only top-level `disposition` and `contract_status`.

Observed result:

- **Completed** in 8 epochs with 8 tool calls and 6 model calls.
- The first generated program had a pure read fault after `spawn_agent`; the next phase repaired it without replaying the spawn effect.
- The parent then executed `wait_agents`, `collect_agent`, and `review_agent`. The review observation was accepted/pass, and the final value included the complete L2 result `{layer:2, answer:4}` and root answer `4`.
- An initial `update_work` attempt tried to replace an immutable required goal and was rejected by the host. The model consumed that observation, retried with the preserved required goal, and the completion gate passed. `claims_verified` remained false, as expected for an arithmetic child with no source-truth claim.

Cross-output comparison: Round 71b's GLM-5 three-level task reached collection/review but failed while consuming nested review/identity data and exhausted 12 model calls. Round 72 reaches accepted review and final completion on a two-level task using the new host summary. This is evidence for a narrower interface improvement, not proof of broad nested reliability.

## Limitations and next focus

The three-level and deeper GLM-5 paths still have unresolved child program generation and recovery failures. The new fields do not prove factual correctness and do not relax review, evidence, or contract checks. A successful non-GLM deep run plus repeated GLM runs are still required before claiming reliability across the requested task classes.
