# Engineering Round 32 — identity-boundary diagnostics

## Scope

Round 31 showed that models can still dereference child identity envelopes as if they were result objects. This round adds a semantic diagnostic at the VM boundary: when a `get` misses a key on an object containing `agent_id`, the fault remains `MISSING_KEY` but explains that the value is a child identity envelope and only the opaque `agent_id` may be passed to wait/read/review. No value is synthesized, no field is renamed, and no contract or evidence check is weakened.

## Offline verification

- `PYTHONPATH=src python3 -m unittest tests.test_kernel_invariants tests.test_general tests.test_collaboration tests.test_recovery tests.test_coordinator_recovery tests.test_handoff_authority tests.test_projected_observe tests.test_frontend -q`
- Result: **146 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.
- The new kernel check confirms the fault code remains `MISSING_KEY` while exposing the opaque-ID guidance.

## Live API validation

Endpoint: user-provided OpenAI-compatible gateway; model `glm-5`.

Run: `/private/tmp/flora-round32-glm-onechild`

- The model attempted the minimal one-child spawn/read/review task.
- The VM emitted the new `opaque agent_id` diagnostic three times, so the observed identity-boundary error was surfaced rather than silently transformed.
- GLM-5 still ended with an incomplete bundle missing `programs`, `incumbent`, `diagnostics`, `expected_epoch` and `expected_digest`; the compiler returned `needs_program` after two repair attempts.
- No child result was published as success and no side effect was executed.

Cross-output comparison with the previous DeepSeek/GLM runs shows the diagnostic changes the reported cause from opaque-envelope field access to a later bundle-generation failure, but does not by itself make the model produce a valid program.

## Limitations

- The diagnostic improves attribution and repair context, not end-to-end model reliability.
- GLM-5 still produces incomplete or structurally invalid bundles in this task family.
- DeepSeek three-level success and five-level failures remain the current evidence boundary; arbitrary deeper nesting and mixed side-effect tasks are unproven.
- No external factual claims were established; `claims_verified=false` remains correct.
