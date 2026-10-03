# Engineering Round 78 — batch contracts, default recursion, and anchored composer

## Scope

This round keeps the dual-control and synthesizable-contract boundary while
closing three host-side reliability gaps and correcting the interactive layout.

* `spawn_agent` and `spawn_agents` now share handoff preparation. Batch
  signatures include the same normalized dependency IDs and evidence context
  used by the single-item path. Duplicate semantic handoffs are rejected before
  a child starts; a retry of an already recorded batch reuses its IDs and does
  not consume the child quota. All batch specifications are validated before
  admission, so a malformed later item cannot leave an earlier child running.
* Recursive coordinators use the same bounded child provider idle-window guard
  as the legacy delegation path. The parent provider remains unchanged.
* The guided `flora` launcher fills bounded defaults (`max_depth: 5`,
  `max_total_children: 64`) while preserving explicit values, including an
  explicit `max_depth: 0`. A normal launch therefore exposes recursive
  delegation without requiring an internal profile.
* The Rich execution view reserves a bottom composer panel. Before an idle
  prompt opens, the terminal cursor is anchored to the row above its toolbar;
  the input line itself therefore stays at the bottom while result cards remain
  in scrollback. Existing pixel art, colors, panels, `/details`, and `/log`
  remain available.
* Completion guidance now distinguishes empty evidence accepted by host child
  review from fabricated source/file evidence. Child review records and result
  digests cannot be submitted as source evidence.

These are host mechanisms, not task-specific decompositions. An observation
from a rejected duplicate, a child contract failure, or a compiler fault changes
the next model branch; the host does not silently coerce it into success.

## Offline verification

* Targeted collaboration, completion, recovery, terminal-default and UI-adjacent
  regressions: **84 passed**.
* `ruff check`, `compileall`, and `git diff --check`: passed.
* Full discovery: **479 tests, 1 environment-sensitive error**. The failing
  evidence-coverage fixture hit the document parser's 30-second deadline while
  parsing an oversized CSV in this environment; it was not a changed assertion.

## Real API verification

Endpoint: `http://35.220.164.252:3888/v1`, with the user-provided key. The
following runs used the current worktree and the same seven-child task.

| Run | Model | Observed result |
| --- | --- | --- |
| Round 83 | `glm-5.3` | 7 distinct child IDs, all 7 collected and reviewed, completed in 89.1 s (4 model calls, 11 tool calls). The final answer remained explicitly unverified for mathematical truth. |
| Round 84 | `deepseek-v4-flash` | 7 children collected; 6 reviews passed and one contract mismatch was recorded as blocked. The final answer reported the missing accepted item instead of claiming 7 successes; completed in 242.2 s (4 model calls, 18 tool calls). |
| Round 85 | `glm-5.3`, guided defaults | The actual default profile reported `max_depth=5` and `recursive_delegation=true`. The nested task then ended `needs_program` after a child compiler repair (`resume2` received an undeclared argument); no nested success is claimed. |

The two seven-child runs show that semantic batch rejection and contract
review work across different output styles. They do not prove broad model
reliability or mathematical correctness.

## Remaining limitations

1. `evidence_requirements` are still largely descriptive at child review time;
   the next round must bind structured source/file witnesses to actual child
   read receipts and reject parent-only or empty evidence when a contract
   requires a source observation.
2. The cumulative `max_total_children` admission count is currently in-memory;
   restart recovery needs a durable admission ledger so a new process cannot
   reset the fan-out bound.
3. Deep nested programs can still exhaust compiler repair budget or emit an
   invalid continuation signature. The default-depth change exposes the
   capability but does not hide this model/compiler limitation.
4. The full terminal cursor behavior was smoke-tested through a pseudo-terminal
   escape capture; a human interactive run should confirm behavior with the
   user's terminal emulator and window resizing.

