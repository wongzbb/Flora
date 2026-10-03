# Engineering Round 70 — Minimal phase repair after compiler faults

## Scope

This round changes only the compiler's repair guidance. When a generated program is rejected because of block parameters, continuation arguments, or an undefined local, the next model call is asked to generate the next necessary effect plus one minimal continuation block. Later consumers and branches are deferred until that observation exists. The guidance also explicitly forbids replaying an already successful effect. This is a phase handoff driven by the validation observation; it does not weaken schema checks or infer missing fields.

The change is intended to support the dual-control design: an action produces task progress and an observation, and the observation selects the next executable phase. It keeps the synthesizable-contract requirement intact because target parameters, result shapes, dependencies, and review evidence remain validated by the runtime.

## Offline verification

- `tests.test_compiler_advisory`, `tests.test_projected_observe`, `tests.test_recovery`, and `tests.test_collaboration`: **87 passed**.
- `ruff check src tests`: passed.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.

A regression test checks that a validation repair prompt contains both the minimal next-effect instruction and the no-replay constraint.

## Real API validation

Endpoint: configured Flora API (`/v1`), model `glm-5`, nested root → L2 → L3 task with explicit contracts, wait/read to `next_offset=null`, and digest-based review.

Observed execution:

- 14 executable epochs, 14 tool calls, and 12 model calls.
- The first child attempt executed `spawn_agent`, `wait_agents`, and `read_agent`; the runtime rejected a child program whose `read_check.term` passed an undeclared `last_off` parameter. No side effect was replayed by the host.
- Subsequent model calls generated additional phases, but repeated failures remained in result-shape handling (`MISSING_KEY`, `TYPE_ERROR`) and review/unfinished-child handling. The final run ended with `budget_exhausted` / explicit model-call limit; the required nested task was not completed.
- The runtime retained strict review behavior: the unfinished child was not accepted as completed, and `claims_verified` remained false.

This is a negative cross-model result, not a success claim. It shows that staged repair guidance is being applied after observations, but GLM-5 still does not reliably synthesize the nested read/review contract. Earlier DeepSeek rounds completed three- and five-level explicit-contract chains, while prior GLM v2/v3 and observe-v1 runs also failed at different interface boundaries. The evidence therefore supports continued work on model-independent contract/phase interfaces rather than more format-specific patches.

## Limitations and next focus

The current change does not solve semantic planning, nested result-shape construction, or budget exhaustion. It only narrows repair scope after a compiler fault and protects already completed effects from replay. Next work should improve the machine-readable phase handoff so a repaired continuation receives the exact observed result envelope and contract obligations without asking the model to reconstruct them from a large history, while preserving strict validation and evidence-before-acceptance.
