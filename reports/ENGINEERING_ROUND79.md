# Engineering Round 79 — semantic action lowering, durable admission, and trace witnesses

## Why this round

The two user prompts that repeatedly ended in `needs_program` exposed an
architectural failure: the model was asked to choose the work and also wire
every low-level continuation parameter. A useful child fan-out could therefore
finish, while the parent was rejected only because a later block passed extra
registers. This round adds a semantic action boundary. The model may describe
bounded calls, saved observations, conditionals, loops and replans; the host
lowers that description to the existing explicit effect/branch/continuation IR.
No result is inferred and no effect is executed during lowering. Existing IR,
schema, anchor, budget and trace validation still run after lowering.

## Reliability changes

* `src/flora/language/structured.py` compiles a semantic action plan into
  lexical scopes and mechanically generated continuation frames. Tool errors
  replan with their actual outcome by default; an explicit `on_error` plan is
  required for an alternate path. Loops and branches remain finite and bounded.
* `LLMCompiler` requests the semantic protocol when a large low-level bundle or
  a continuation/interface failure cannot be repaired safely. Tiny syntax probes
  and ordinary explicit block diagnostics retain the previous repair path. This
  is a semantic fallback, not a delimiter or model-specific output patch.
* `max_total_children` is persisted in an admission ledger shared by recursive
  coordinators. Reopening a session cannot reset the cumulative fan-out count;
  retrying an existing handoff still reuses its ID.
* Structured contract evidence requirements can require a real successful
  `file_read` or `source_read` witness. Review obtains witnesses from the child
  SQLite trace, records their hashes/completeness, and rejects acceptance when a
  required witness is absent. Descriptive string requirements remain backward
  compatible and are not treated as machine-verified evidence.

## Offline verification

* Structured planner, malformed-large-bundle and real-effect execution
  regressions: **6 passed**.
* Collaboration, restart budget and trace-witness regressions: **45 passed**.
* Existing compiler/general regressions: **40 passed**; coordinator/recovery/
  completion/terminal targeted set: **42 passed**.
* Full discovery: **485 tests, 1 known environment-sensitive error**. The
  existing oversized-CSV fixture exceeded the document parser's 30-second
  deadline; no assertion failure was introduced.
* `compileall` and `git diff --check`: passed.

## Real API verification status

The required endpoint was unavailable during this round. Both fresh runs of the
exact user prompts (`glm-5.3`, the supplied endpoint and key) failed before any
request was dispatched; an independent `curl /v1/models` also returned connection
refused. These are **unknown transport results**, not evidence of task success or
failure. The previous live runs remain historical evidence only: they showed
child admission/review behavior but did not validate this new semantic fallback.

## Remaining limits

The semantic fallback still depends on the model returning a strict semantic JSON
object on its bounded repair call. It removes continuation wiring from the model,
but does not prove that the model chose a complete decomposition or that a child
answer is factually correct. Live verification across `glm-5.3` and a second
available model is required before this round can be pushed or considered a
validated improvement.
