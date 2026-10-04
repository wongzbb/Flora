# Engineering Round 82 — durable nested waiting

Date: 2026-10-04  
Branch: `general-agent`  
Base: `fe88409`

## Scope

This round fixes a host lifecycle bug in recursive delegation. A worker runtime
can return `waiting` when its completion guard observes required nested workers
still running. `Coordinator._run` previously continued only for `yielded`; it
then closed the nested coordinator in `finally` and persisted the parent result.
That discarded the live nested futures and made a pending child look like a
terminal failure. The change keeps the same kernel, trace, contracts and
cumulative budget, waits for a real nested boundary, and resumes the worker.

The loop still preserves `waiting` when there is no nested coordinator. In that
case the host has no future it can safely poll, so it returns a resumable
observation instead of polling the model or fabricating progress.

## Core design preserved

- **Dual control:** the wait is tied to the actual nested-worker completion
  observation. The observation changes the next resume decision; no synthetic
  reflection or duplicate child is created.
- **Synthesizable contracts:** nested completion, collection, review and
  evidence gates remain unchanged. A child that exhausts its budget remains
  unaccepted and blocks the parent; the host never converts that claim into a
  successful contract result.

## Verification

- Focused offline regression: **71 passed** (`coordinator_recovery`,
  `handoff_authority`, `task_completion`, `recovery`).
- Full offline discovery: **487 passed** in 236.75 seconds.
- Real API, DeepSeek, four-level nested task, depth 4: the run reached L2 → L3
  → L4 and the lower workers actually completed. The parent remained live while
  nested workers were running; no stale `waiting` result was accepted as
  completion. The run eventually ended `budget_exhausted` after 917.5 seconds:
  repeated long model compilation exhausted child budgets, and the root
  completion gate reported the failed/unreviewed child. This is a mechanism
  pass for lifecycle safety, not a task-success claim.
- Real API, GLM-4.5-Air: the endpoint returned HTTP 503 on all three transport
  attempts, including a simple control task. Usage was recorded as unknown and
  no task result was accepted. This is an endpoint availability failure, not
  evidence about the scheduler.

## Remaining limitations

Complex nested tasks can still spend most of their budget in model compilation
and may end `budget_exhausted` or `needs_program`. This round prevents premature
termination and false success; it does not claim broad natural-language
planning reliability. The next robustness round should reduce semantic phase
size and improve bounded recovery from compiler exhaustion without adding
model-specific output patches.

