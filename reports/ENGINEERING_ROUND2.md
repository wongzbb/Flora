# Versioned delegation constraints and natural mechanism evaluation

Continuation from `351abb904ed048985e2d52efe92e1b129fff57a6`, 2026-10-01.

## Changes and scope

The repeated release-audit type failure began when a parent changed the user's
number requirement into a string requirement. Coordinator already saved the
original task and passed it in memory.data; this change does not claim that it
was previously absent. For newly created schema-4 workers, the saved original
task and assigned subtask now form the actual canonical JSON task. Ordinary
memory omission cannot discard just the original task. A version marker preserves
the identity of old worker records. Missing/invalid new origins and overlarge
tasks fail before a model call, with no silent constraint truncation.

Application guidance tells the worker to preserve applicable original user
requirements while completing only the assigned subset, and to report unresolved
conflicts. Quoted documents and dependency claims remain untrusted data. Tool
grants are unchanged. This is source/priority preservation, not host semantic
validation or automatic number conversion. Guidance uses the existing Agent task
context; it is not a newly introduced system-message authority tier.

New versioned workers also receive consistent compact-v3 phase guidance. Previously
the parent had the phase rule while Coordinator still supplied the old semantic-
reasoning-only rule to workers. Old unmarked worker identities remain unchanged.

The authority profile changes only schema 3 to schema 4 relative to the phased
profile. It enables both original-task handoff and the prior work-refinement
interface; differences between those profiles cannot isolate either component.

The dispatch mechanism probe now supports `--elicitation natural`, which removes
only the task-level request to construct alternatives, forecasts and diagnostics.
The compiler still teaches all mechanisms. Default explicit task text is unchanged.
Task success and actual diagnostic insertion retain separate grades; ordinary
observe/compute/commit can pass the task without passing the mechanism. Neither
grade establishes causal benefit. Saved action trajectories link unique selections
and real receipts by epoch, trace digest and tool; no extra tool executes for grading.

Reliability experiments also record observer time to the first tool dispatch,
generated program transcript UTF-8 bytes, validation refusals and replan count.
These are operational observations, not tokens, lowered-program sizes or proof
of useful task progress. No generated text is copied into these metrics.

## Validation

Independent reviews covered worker authority, saved identity, scope/permission
limits, phase consistency, telemetry concurrency and the natural-mode oracle.
The source suite passed **393 tests**, one platform skip, in **194.393 seconds**.
The newly built and installed wheel passed the same **393 tests**, one skip, in
**192.770 seconds**. No runtime or test changes followed those complete runs.
The installed Coordinator bytes match the source. Pip check, Ruff and diff checks
passed. Logs: `/workspace/scratch/flora-round2-source-full.log`,
`flora-round2-wheel-full.log`, and `flora-round2-build.log`.

These deterministic checks prove neither model compliance with original constraints
nor a semantic contract's truth. Real acceptance is still required.

## Live continuation

The Pro compact-v3 routing comparison was started from clean `351abb9` with the
original seed 20261005, original phased profile and bounded resume allowance. At
this code publication it remains active under execution session 74765, output
`/workspace/scratch/flora-round2-phased-pro-01`. It has dispatched actual worker
and parent tools and repaired a source bind error, but no completed result is yet
claimed. No authentication/balance denial has been observed. Do not duplicate it.

Next: inspect that completed result, run the schema-4 release-audit comparison,
then both models on seed 20261019 across type/structure, exact edit, decimal
reconciliation and multi-agent routing families. Run natural dispatch separately.
Check actual source windows and publication receipt counts, not only path presence
or a final file. New seeds within existing fixtures are within-family holdouts,
not a wholly new task distribution. Record failures and long latencies honestly.

Open: type drift, natural work decomposition/refinement, diagnostic utility,
Pro latency/program generation, broad long-range reliability. No default was
changed and no universal success claim is made.
