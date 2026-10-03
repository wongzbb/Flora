# Engineering Round 31 — opaque child identity semantics

## Scope

Round 30 showed a concrete cross-model failure: GLM-5 treated a child spawn identity envelope as a nested business result and attempted to read `name/status` through it. This round makes the distinction explicit in the generic tool result descriptions and in parent/child collaboration instructions:

- `spawn_agent` returns an identity envelope; only its opaque `agent_id` may be passed to wait/read/review.
- `spawn_agents` returns stable `agent_ids`; child answers are obtained independently.
- Identity metadata (`name`, `status`) is not a child answer and cannot satisfy a result contract.

No runtime result is renamed or coerced. The host still unwraps only the authorized identity field at the tool boundary and keeps result collection/review separate.

## Offline verification

- Collaboration, coordinator recovery, handoff authority, frontend, projection and general regression: **126 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API comparison

The user-provided endpoint was reachable in the permitted network environment.

### GLM-5, three-level nested chain

Run: `/private/tmp/flora-round31-glm-nested3`

The earlier identity-envelope error disappeared after the new guidance. The model then generated a `read_more` block with an undefined variable; the compiler rejected it after two repairs. No child result was published.

### DeepSeek `deepseek-v4-flash`, same three-level chain

Run: `/private/tmp/flora-round31-deepseek-nested3`

This run reached the compiler but returned invalid JSON after two attempts. It was rejected with `needs_program`; no fabricated result was accepted.

### GLM-5, minimal one-child spawn/read/review

Run: `/private/tmp/flora-round31-glm-onechild`

The model made progress through several phases, but ended with an incomplete block-list program after `MISSING_KEY` observations. The compiler rejected the final program. This separates the identity-envelope improvement from the remaining general program-generation problem.

For comparison, the immediately preceding Round 30 DeepSeek run with the same wait/read semantics completed a three-level chain and returned `7`; that success remains valid evidence for the prior code and is not counted as a success for this new prompt-only change.

## Limitations

- The new semantics remove a specific identity/result confusion but did not improve end-to-end GLM or DeepSeek success in these direct reruns.
- Invalid JSON, undefined variables and incomplete block programs remain model-generation failures; the host rejects them without changing execution or evidence conclusions.
- Deeper nesting beyond the successful three-level DeepSeek run remains unproven. No external factual claim was established, so `claims_verified=false` remains correct.
