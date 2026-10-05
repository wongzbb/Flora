# Engineering Round 95 — bounded semantic recovery

## Scope

Round 94 showed that GLM 5.3 could repeatedly spend child budget producing
invalid semantic plans. This round changes the generic compiler boundary and
recovery policy, without adding a model-specific answer or tool path.

## Changes

- The semantic JSON boundary can repair at most two decoder-reported missing
  commas when there is exactly one resulting strict JSON object. Ambiguous,
  duplicate-key, nonfinite, multi-error and schema-invalid responses remain
  rejected. Lowering, capability checks, contract checks and anchors are not
  bypassed. A one-comma repair keeps the existing event label; two are recorded
  as `insert_missing_commas:2`.
- After semantic parse or validation failure, correction mode asks for the
  smallest complete executable phase: at most one effect (or one replan) and
  one terminal. This prevents a failed large plan from being regenerated as
  another large plan. The host continues the task at the next observation.

This is a general representation boundary and bounded phase policy. It does
not inspect model names, patch a particular field, coerce values, or weaken
contracts/completion gates. The observed compiler rejection changes the next
action phase, which is the dual-control requirement.

## Offline verification

- Structured planner and compiler advisory tests: **39 passed**
- Collaboration, coordinator recovery, task completion, kernel invariants,
  structured planner and compiler advisory together: **144 passed**
- `python -m compileall -q src`: passed
- `git diff --check`: passed

New tests cover two unique missing commas and correction-mode guidance.

## Real API cross-model verification

Task used for both runs:

> 你能调用5个子agent，每个子agent调用1到3个工具做不同的是事情，具体做什么你来决定，请开始

The same endpoint, profile and parent/child budgets were used; API keys are
intentionally omitted from this report.

### GLM 5.3

- `completed`, host completion `ready=true`.
- Five logical children, each used 1–3 read-only tools; all five were collected
  and accepted.
- Parent: 5 tool calls, 10 model calls, 113.6 seconds.
- No replacement was needed in this run.

### DeepSeek Flash

- `completed`, host completion `ready=true`.
- Five initial children were collected and reviewed. Two contract-shape
  violations were explicitly blocked, then replaced with revised required
  contracts; both replacements were collected and accepted. Historical blocked
  rows remain in `superseded_workers`.
- Parent: 14 tool calls, 6 model calls, 305.6 seconds.
- The final answer reports the network search limitation and missing fields
  rather than treating empty/absent observations as facts.

These runs are evidence that the bounded recovery reduces the previous GLM
`needs_program` failure for this task and works across two model output styles.
They do not establish broad reliability, factual truth, 7-calculus success, or
deep nesting success.

## Remaining limits

The compiler still has finite budgets and cannot infer the meaning of an
arbitrary task when the model never emits a valid phase. A root model can still
write a misleading free-form summary; host completion and review observations
remain authoritative. The next required evaluations are the 7-calculus
children, multi-layer tool task, and a nested chain deeper than three levels,
with their actual child receipts inspected.
