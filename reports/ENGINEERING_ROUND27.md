# Engineering Round 27 — information-gain guard and live cross-model evaluation

## Scope and design constraint

This round addresses a general execution failure exposed by live GLM-5 behavior: a program can successfully execute a tool, receive the same value, fail the durable completion gate, and then replay the same program indefinitely. The host now tracks an exact signature of the candidate program and its observed return value at `consumer_check`. Three identical observations without an intervening value change pause the run with an explicit request to revise the plan or record a limitation. A changed observation resets the counter. This is a dual-control guard: repeated action is stopped only when it produced no new information; changing observations remain available to drive replanning.

The round also retains the previous contract improvement: child tool descriptions state the actual result object fields for file, source, capability, skill, workspace and artifact inspection tools. These descriptions are boundary documentation derived from the implementations; contract review still checks the observed result and does not coerce field names.

## Offline verification

- `PYTHONPATH=src python3 -m unittest tests.test_general tests.test_collaboration tests.test_recovery tests.test_coordinator_recovery tests.test_projected_observe tests.test_frontend -q`
- Result: **116 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation

Endpoint: the user-provided OpenAI-compatible gateway at `http://35.220.164.252:3888/v1`, accessed in the permitted network environment. The earlier connection failures were caused by the restricted execution environment, not by endpoint unavailability.

### DeepSeek `deepseek-v4-flash`: five-agent read-only task

Run: `/private/tmp/flora-round26-deepseek-tools-live`

- Completed in about 164 seconds with 5 children and full read/review collection.
- Two child contracts were satisfied.
- Two children were explicitly blocked because the requested tool was not granted or the observed type contract differed; one additional branch was limited by the available tool set. The final value preserved these limitations and did not rename fields into compliance.
- The task recorded `no_network=true` and `side_effects=false`.

### GLM-5: five-agent read-only task

Run: `/private/tmp/flora-round26-glm-tools-live`

- The endpoint and model were reachable, but GLM-5 generated an invalid `spawn_agents` program twice: `tasks` appeared at the term level instead of inside `args`.
- The compiler rejected the program before any child dispatch. No tool calls or side effects were recorded.

### GLM-5.3 Flash: same task

Run: `/private/tmp/flora-round26-glm53-tools-live`

- The model reached the compiler but emitted truncated JSON after two attempts.
- The compiler rejected the malformed bundle; no child dispatch occurred.

These two GLM outcomes are retained as cross-model evidence: the host did not turn invalid transport/program output into semantic success, and the failure was distinguishable from a runtime contract violation.

### DeepSeek `deepseek-v4-flash`: completion task after stagnation guard

Run: `/private/tmp/flora-round27-deepseek-stagnation`

- Completed in about 41 seconds, 3 model calls and 3 tool calls.
- It called `workspace_context`, read the required work item, updated the immutable goal with the observed fields, and finished with `completion_checks.ready=true`.
- The trace shows the observation caused a `replan_requested` event and a later `update_work` action; it did not repeat the same tool.

### GLM-5: completion task after stagnation guard

Run: `/private/tmp/flora-round27-glm-stagnation`

- Completed in about 206 seconds, 5 model calls and 4 tool calls.
- The model initially tried an invalid work update and received the real immutable-goal error. It then corrected the goal text, successfully marked the task completed, and returned the three observed paths.
- The run ended with `completion_checks.ready=true`, not budget exhaustion. The changing program/result signatures prevented the guard from suppressing legitimate recovery.

## Limitations

- The five-agent DeepSeek task still contained blocked branches; this is correct contract behavior, not proof that model-authored contracts are broadly reliable.
- GLM-5 and GLM-5.3 still fail some complex program-generation attempts before dispatch. The compiler preserves the failure boundary but cannot make arbitrary model output valid.
- The information-gain guard detects exact repeated program/value pairs. It does not prove semantic progress when values differ but remain unhelpful.
- None of these runs established external factual claims; `claims_verified=false` remains correct. Arbitrary deeper nesting, larger fan-out, and side-effect recovery require more live repetitions.
