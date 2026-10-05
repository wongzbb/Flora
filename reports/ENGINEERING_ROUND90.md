# Engineering Round 90 — phase scope and pure observation normalization

Date: 2026-10-05  
Branch: `general-agent`  
Base: `a54ac33`

## Change

Semantic phase prompts now state an explicit scope rule: every compiled phase
starts with an empty local environment. Values from an earlier program must be
recovered from the actual trace or obtained again; they are never assumed to
survive a replan or completion-gate retry. This makes the phase boundary part of
the executable contract instead of relying on a model to preserve hidden local
names.

The structured language accepts the canonical three-argument
`get_default(object,key,default)` and a safe two-argument shorthand only when
the first value is a one-key field projection. It also accepts readable
call-shaped `read_receipt` and `read_memory` forms, but lowers them to pure
operations and never to effect terms or tool receipts. The prompt documents
that these operations are observations of the existing trace/memory, not tools.

Semantic stream-progress failures now receive a bounded fresh complete-phase
request after transport fallback, instead of terminating before semantic
recovery. Previously executed effects are not replayed because no partial
program is installed.

## Verification

- Structured planner, collaboration, coordinator recovery, transport recovery
  and frontend regression: **153 passed** in 7.68 s.
- Real GLM 5.3 three-child task: the parent spawned all three children and
  issued an explicit replan. Subsequent semantic phases still failed across
  bounded attempts (first a pure `get_default` arity mismatch, then a phase
  receipt-reading shape issue, and finally malformed JSON in a later run). The
  task is not counted as complete.
- Real DeepSeek Flash simple task completed with the current code in about 10 s
  after a completion-gate replan. This is a cross-model smoke comparison only.

## Limits and remaining work

The host now makes phase scope and pure receipt access explicit, but complex
models can still produce long malformed phase plans or choose an invalid
receipt path. The three/seven-child collaboration tasks and the five-worker
multi-tool task remain unresolved for broad GLM behavior. Future work should
reduce the amount of semantic state required per phase and test nested
delegation, while retaining explicit observations and contracts rather than
adding more model-specific syntax repairs.
