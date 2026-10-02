# Engineering Round 25 — contract revision across nested and multi-tool work

## Scope

This round targets the reliability gap where a nested worker returns a primitive/object shape that does not match the parent contract. The implementation keeps the original receipt nonconforming and makes the observation drive the next program choice. Worker guidance now requires one of three explicit branches: consume the result with a pure parent-preserving wrapper when the primitive already satisfies the parent guarantee, issue a replacement handoff with a revised contract, or block the branch. Coercion or silently accepting the original receipt is prohibited.

The compiler changes used in this round are bounded boundary recovery only. They accept a unique, semantically neutral missing-delimiter repair when the complete candidate bundle parses; they do not infer a program, change values, or weaken contract checks. The normal-program diagnostic also states that candidate metadata must remain outside the labelled program array.

## Offline verification

- `PYTHONPATH=src python3 -m unittest tests.test_recovery tests.test_collaboration tests.test_coordinator_recovery tests.test_projected_observe tests.test_frontend tests.test_general -q`
- Result: **113 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed in the implementation round.

## Real API evaluations

The evaluations used the supplied OpenAI-compatible endpoint and an execution budget of 12 model calls, 80 tool calls, and 900 seconds.

### DeepSeek: four-level nested delegation

Run: `/private/tmp/flora-round25-nested`

- Status: `completed`; value `{"result": 42}`.
- The nested chain reached depth four, completed the child handoffs, and returned through the parent reads and review.
- `contract_check=ok`; completion gate was ready; 3 model calls and 8 tool calls; about 263 seconds.
- This is evidence that a primitive result can be carried through an explicitly revised primitive interface and a pure parent wrapper without accepting the earlier nonconforming object receipt.
- The task had no external factual source requirement, so `claims_verified` remained false as expected.

### GLM-5: five agents with independent read-only tools

Run: `/private/tmp/flora-round25-glm-tools`

- Status: `completed`; all five child branches were spawned, read, reviewed, and included in the final bundle; completion gate was ready.
- Two branches were accepted (`list_sources`, `list_skills`).
- Three branches were blocked with explicit contract evidence:
  - `list_files` returned an object containing `entries`, while the child contract required a `files` array.
  - `agent_capabilities` returned `tools`, while the child contract required `capabilities`.
  - `artifact_status` was unavailable/not granted and therefore produced no result.
- The parent summary preserved these blockers and did not coerce field names or treat a self-reported completion as success. All child actions were read-only. `claims_verified` remained false because no external source-backed claim was required or established.

## Cross-model interpretation

DeepSeek exercised the nested contract-revision path; GLM-5 exercised broad fan-out, independent tool calls, complete readback, and rejection of incompatible result shapes. The two runs show that the mechanism is not tied to one model's JSON style: output compatibility is checked at the contract boundary, while bounded syntax recovery only repairs a uniquely identifiable transport boundary. They do not establish broad success for arbitrary prompts, deeper branching, external evidence tasks, or exhausted budgets.

## Remaining limitations

- The successful nested run is one concrete four-level task; arbitrary nesting depth, concurrent replacement cascades, and larger fan-out still need repeated evaluation.
- GLM-5 exposed real schema/contract mismatches that were correctly blocked; model-generated contracts still need to describe actual tool result shapes more reliably.
- These tasks did not prove source-content coverage or factual claim verification; `claims_verified=false` is retained.
- Budget exhaustion and malformed/partial model programs remain possible. The implementation records those observations and gates completion, but cannot guarantee recovery for every failure.
