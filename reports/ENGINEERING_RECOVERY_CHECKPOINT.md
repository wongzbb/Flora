This historical recovery snapshot is superseded for the pending runtime change by [TASK_COMPLETION_CHECKPOINT.md](TASK_COMPLETION_CHECKPOINT.md), which records its completed validation and portable evidence.

# Engineering recovery checkpoint — 2026-10-01

This checkpoint supersedes older statements that the comparison, diagnostic probe, consumer recovery, or release audit is still running. No new live call was made during recovery. Current process inspection found no active live probe or unittest process; old PTY IDs 80294 and 18641 are no longer registered.

## Verified baseline

Before this checkpoint, local and remote general-agent both resolve to 088e3e95c2ee165692da6285e09caa1bc17de654. Its opt-in block-list-v3 source projection is already published. Detached /workspace/scratch/flora-source-v3 remains at 01c32563670b9b58dcc092acb1d9ec0437c86b4c with its original uncommitted projection files; these are retained, not a pending second merge. Source log /workspace/scratch/flora-source-v3-full.log records 333 tests, one skip, passing in 200.618 seconds (old session 18641). Fresh installed-wheel log /workspace/scratch/flora-source-v3-wheel-full.log records the same count passing in 199.869 seconds. No other branch is changed.

## Completed live evidence

- Old session 80294: Pro nonthinking dependency_route comparison finished with program_validation / needs_program, no task pass, 130.707 seconds, 4 model calls, zero tool calls, 43,091 input and 24,717 output tokens. Result: /workspace/scratch/flora-live-pro-nonthinking-resume-01/evaluation.json. Disabling thinking did not solve source generation. Historical metadata omitted reasoning_effort=none; do not rewrite it as if captured.
- Old session 98423: release_audit completed for both models. Flash failed the external grade after runtime completion (168.625 seconds; 10 calls; 146,224 input / 39,220 output); the release was a string instead of the required number. Pro passed (583.5 seconds; 9 calls; 127,766 input / 67,186 output). Both preserved receipt and budget counters across reopen. Neither used a diagnostic or revision. Result: /workspace/scratch/flora-live-v3-release-audit-resume-01/evaluation.json.
- Elicited diagnostic probe: Flash task pass 2/2, actual mechanism pass 1/2 (variant 2 linked insertion and forecast feedback); Pro task pass 2/2, mechanism pass 0/2. Shared normal reads do not count as inserted diagnostics. Results: /workspace/scratch/flora-live-diagnostic-{flash,pro}-resume-01/results.json. This is not spontaneous synthesis or causal-benefit evidence.
- Consumer recovery: both models passed both read and publish repairs without repeating the successful effect. All four had zero accepted revisions. Results: /workspace/scratch/flora-live-consumer-{flash,pro}-resume-01/results.json. These host-seeded failures do not establish mixed successful-history preservation.

## Saved implementation state

Recovery reran 9 task-completion tests plus 7 revision-probe tests: all 16 passed in 3.320 seconds. git diff --check passed. The independently reviewed revision experiment and its seven offline tests are included in this checkpoint; the experiment has not been run with a real model. It seeds two actual page reads with a successful old boundary and a subsequent consumer fault, then grades model-authored EXTEND history/current checks, activation, execution and exact tool history separately from task correctness.

The runtime task-completion changes remain uncommitted in src/flora/general/agent.py and src/flora/general/work.py, with tests/test_task_completion.py. They add an opt-in durable root obligation and immutable required goals; all general-v4 task/work mismatches fail closed. Independent review and focused tests passed, but the final combined source/wheel regressions and real task comparison remain pending. Completion declarations remain model claims, not host semantic verification.

A local copy of all five pending source/test files and the tracked patch is saved under /workspace/scratch/flora-engineering-checkpoint-20261001/. This contains code only. Original workspaces and result directories are retained.

## Next work (not authorized for paid execution by this recovery request)

1. Complete combined source/wheel validation before publishing the pending runtime change; add its explicit opt-in experimental profile.
2. Fix future provenance recording to include reasoning_effort=none and the typed task-completion option; preserve old evidence.
3. Investigate Flash release number/string provenance without weakening the oracle.
4. Run the mixed-history revision probe for both models only after renewed live-call authorization; require actual accepted and executed revision evidence.
5. Measure the durable task obligation against premature route-only completion. Address whole-workflow generation with small executable phases, not larger budgets. Pro latency and reasoning exhaustion remain unresolved.

Overall acceptance remains incomplete. No initialization, credential inspection, or paid API call is part of this recovery checkpoint.
