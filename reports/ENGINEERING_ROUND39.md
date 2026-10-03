# Engineering Round 39 — count semantic replan frontiers

## Scope

Rounds 36–38 showed a deeper scheduling failure. A parent could compile a different program after every `wait_agents`/`read_agent` result while its replan state still said “the same child is running.” The old no-progress guard included the program digest, so these syntactic rewrites reset the counter. This allowed a five-level chain to spend hundreds of seconds cycling through equivalent observations.

The guard now treats a `replan` candidate as the model's semantic observation frontier: its observed candidate state is the progress signature, independent of the newly compiled program digest. A genuinely changed observation resets the counter; merely rewriting the consumer program does not. Effect candidates retain the stricter program/receipt signature. After three identical semantic replans, the existing safe pause path can stop busy children without fabricating a result.

## Offline verification

- Relevant general, kernel, consumer-recovery, collaboration, handoff and coordinator tests: **101 passed**.
- Added a regression proving three identical replan observations pause even when all three program digests differ.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-flash`.

### Before this change

Run: `/private/tmp/flora-round38-dsv4flash-nested5-unavailable`

The same five-level chain created the nested workers but repeatedly regenerated wait/read programs after unavailable-child observations. It ended `needs_program` after approximately 467 seconds and 5 model calls. The trace retained an unfinished child and no final value.

### After this change

Run: `/private/tmp/flora-round39-dsv4flash-nested5-replan-guard`

- The five-level chain completed with numeric value **7** after approximately 631 seconds, 11 tool calls and 8 model calls.
- The root observed an initial spawn consumer fault, a rejected review of an unfinished child, and a later wait/read cycle. It used `resume_agent`, waited again, fully read the child, reviewed it, and then updated the durable task ledger with the exact required goal still intact.
- The final work step records that level 2 delegated through levels 3–5, each parent waited, read to `next_offset=null`, reviewed its child, and confirmed the numeric result. `completion_checks.ready=true`; no child remained pending or unreviewed.
- `claims_verified=false` remains correct: the task is arithmetic and establishes no external factual claim.

This is a cross-output improvement from an equivalent five-level run ending `needs_program` to one completing through the same host gates. It does not establish broad reliability: the successful run was one DeepSeek Flash task and consumed a large latency budget.

## Limitations

- Five-level success is now demonstrated once, but latency (~10.5 minutes) and 8 model calls are too high for a broad reliability claim.
- Seven-way fan-out, mixed tool calls, side effects, GLM/default model behavior and deeper-than-five nesting remain unproven.
- The semantic replan signature depends on the model exposing a meaningful observed state; an unhelpful but changing state can still evade the guard.
