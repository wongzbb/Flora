# Engineering Round 49 — actionable IR argument diagnostics

## Scope

Complex model-generated programs can fail before any effect executes when a
continuation passes the wrong parameter set to a block. The validator already
rejects this safely, but its message only named the target block. That leaves a
repairing model to infer the expected interface from a large program.

The IR validator now reports both the expected parameter names and the actual
argument keys (or the received value type). This is a generic interface
diagnostic for every `jump`, `branch`, `call`, and `alternative`; it does not
rewrite the program, relax validation, or encode a task-specific solution.
The semantic contract and evidence gates remain unchanged.

## Offline verification

- Projected-observe, coordinator recovery, general, collaboration, handoff,
  and kernel tests: **115 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and
  `git diff --check` passed.
- Regression coverage now requires argument-key diagnostics to include the
  expected and actual interface sets.

## Live API validation and cross-model/output comparison

### DeepSeek-v4-flash

Run: `/private/tmp/flora-round49-dsv4flash-seven-calculus-diag`

The seven-child calculus task completed in about 137 seconds. All seven
children were required, read through `next_offset=null`, and individually
reviewed. Every `contract_check` was `pass` and every child was accepted; the
parent recorded the work step as completed before returning the seven
solutions. The runtime still reported `claims_verified=false`, so this is
execution/contract evidence rather than proof that the generated mathematics
is factually correct.

### GLM-5

Run: `/private/tmp/flora-round49-glm-seven-calculus-diag`

GLM again stopped before effects after two model calls, this time with
`read_6_ok.term: unknown target 'review_0'`. The run therefore provides no
child-task success evidence. It does show that structural program generation
remains a model-dependent bottleneck even when the validator supplies a more
actionable interface error; no format workaround or semantic weakening was
added.

## Remaining limitations

- The diagnostic improves repair information but does not guarantee that a
  model will repair a second structural error within the available repair
  budget.
- Unknown target diagnostics could still benefit from listing valid block
  labels, and GLM program generation needs independent improvement.
- The DeepSeek success does not establish broad reliability, factual
  correctness, or deep nested-agent success across models.
