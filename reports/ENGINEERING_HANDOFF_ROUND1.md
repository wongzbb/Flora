# Engineering continuation — 2026-10-01

## Scope

Continued from verified clean `f83000c7b910547f31a200e3f571d318553fd969` on
`general-agent`, in `/workspace/Flora-handoff`. The original `work` checkout was
not changed. The previous environment's scratch artifacts were unavailable;
historical claims are taken from the portable reports, not claimed as re-inspected.

This round retains dual-control selection and model-authored program/state
contracts. No task solver, type coercion, oracle input, gate relaxation, effect
rollback or automatic unknown-effect replay was added. New compilation organization
and work refinement are explicit opt-ins, not default behavior or proven fixes.

## Reproduced failures and general changes

Both models initially failed the mixed-history EXTEND probe. Flash assumed two
receipts existed at every checkpoint; the first historical checkpoint had only
one. Pro assumed the current `payload` register existed at historical entry.
The gates correctly refused both. The revision view had merged different histories
with identical register shapes, and migration failure reports omitted the fault
category. The view now includes actual checkpoint identity and receipt count,
without merging same-shaped checkpoints. Omission remains explicit. Failure
reports include only structural boundary/type/error categories, not raw values.
The compiler description now explains that each historical migration sees its
own register set, receipt prefix and memory, followed by an independent current
check. Gate semantics are unchanged.

The probe accepts an explicit bounded `--resume-attempts` option, default zero.
Only an actual all-candidate revision gate refusal permits another run of the
same Runtime. It retains candidates, receipts and cumulative budget; no oracle
controls retry. Authentication denial and unknown effects stop it. Successful
post-change live cases below both passed on their first attempt.

The existing task-completion flag was also tried on the original Flash routing
failure seed. It failed before any tool ran: four generations, 117.081 seconds,
capture/bind mismatches, unthreaded variables and malformed tool context. A
durable root obligation does not fix program construction, nor prove that a
model's completion claim is true. `compact-v3` is a separate experimental
compilation organization: the model may hand off a bounded executable phase with
the existing anchored `replan` state instead of being told to replan only for new
semantic reasoning. It preserves pure consumers, diagnostics and revisions, adds
no automatic host task decomposition, and charges every subsequent compilation.
The existing per-execution no-progress counter is not claimed to persist across
resume. The paired profile changes only prompt style relative to task completion.

Explicit tool schema 4 adds `refine_work` and paged `read_work_history`. The exact
user task and root remain fixed, while model-authored plans may be revised with
fresh successor IDs, reasons and optional actual evidence. Required predecessors
need required pending/running successors. Superseded snapshots remain durable;
the 64-step limit applies to current work, with the existing 1 MiB total storage
bound. Structure and reference integrity do not prove semantic preservation.
Schema 1–3 and the default schema 3 retain their tool identities.

The operational configuration recorder now preserves `reasoning_effort=none`,
the exact boolean completion flag, new prompt style and schema version, while
still excluding arbitrary request options and credentials.

## Live evidence

All calls used the authorized official DeepSeek endpoint and the two authorized
model IDs. No access rejection occurred in the six completed experiments below.
Runs started from the f83000c checkout with evolving tracked edits; exact source
hash manifests and effective settings are retained in
`ENGINEERING_HANDOFF_ROUND1.json`. They must not be described as runs of a later
clean commit. Token counts below are measured input/output, not currency.

| Experiment | Model | Outcome | Seconds | Calls | Input / output tokens |
|---|---|---|---:|---:|---:|
| Mixed-history baseline | Flash | task/mechanism fail; historical migration unavailable | 26.044 | 1 | 6,571 / 6,364 |
| Mixed-history baseline | Pro | task/mechanism fail; historical migrations unavailable | 74.369 | 1 | 6,543 / 7,101 |
| Root-obligation routing, seed 20261005 | Flash | source validation failure, zero tools | 117.081 | 4 | 42,873 / 32,744 |
| Release audit, seed 20261006 | Flash | runtime complete, task/evidence fail | 148.698 | 9 | 120,276 / 36,109 |
| Mixed-history after interface correction | Flash | task + executed EXTEND pass | 44.885 | 1 | 6,693 / 9,999 |
| Mixed-history after interface correction | Pro | task + executed EXTEND pass | 65.460 | 1 | 6,665 / 6,351 |
| Phased routing, same seed 20261005 | Flash | task + evidence pass | 147.436 | 6 | 78,458 / 32,880 |

Both successful EXTEND cases preserved the actual old next-page request including
acknowledged_count, passed the newly defined historical prefix and current check,
activated the accepted consumer and returned the total. Exactly two original page
reads occurred. This closes the absence of any live mixed-success-history revision
example, not natural synthesis, causal improvement, held-out generalization or
arbitrary task reliability. The host authored the legacy fault, not the migration
or repaired consumer. Diagnostic insertion is a separate mechanism and remains
unresolved across models.

The first phased routing sample completed both workers, dependency handoff,
reviews and required independent parent reads. It updated the actual root ledger
with both file hashes. It did not create a task decomposition or use refinement;
therefore it is not evidence of natural work-plan synthesis. At 147 seconds it
also does not establish an acceptable latency, and this single sample cannot
isolate the causal effect of the prompt change. Pro and held-out comparisons remain.

## Type failure reconstruction

The repeated Flash audit failure is delegation semantic drift, not an implicit
runtime conversion. Parent receipt 1 changed the user's `release number` into
`release number string`. Worker `a-aea49e2f9470` read numeric 182 and retained the
number in its first replan state. Its next program returned a literal string
`"182"` with no conversion operation. Parent receipt 4 collected that string;
receipt 7 independently observed the numeric source, but receipt 9 accepted the
worker without a value/type comparison. Receipt 11 wrote the string to audit.json.
All required source reads, missing-primary/fallback behavior and input hashes were
otherwise correct. The oracle's evidence failure includes the incorrectly typed
worker answer; it is not evidence of a missing source read.

An additional parent envelope-access fault was recovered using original receipt
12. The publication was not replayed. Reopen preserved receipts and budget counters.
Neither ordinary recovery nor accepted worker review establishes a synthesized
contract or factual correctness. This type failure remains open.

## Verification and remaining work

Independent review covered revision context semantics, the opt-in phase design and
implementation, work refinement and compatibility. Historical baseline plus new
metadata tests passed 351 source tests (one skip, 170.918 seconds), before the later
runtime changes; that run is not the final regression evidence. The final source
and newly installed wheel suites are recorded at publication below.

Publication validation: source 376 tests in 189.493 seconds, installed wheel 376
tests in 191.405 seconds, each passing with one platform skip. A final independent
review found that an optional historical successor could be deleted and its ID
reused, making lineage ambiguous. The fix tracks all historical successor IDs,
with one new rejection regression. After that narrow correction, a fresh wheel
was built and reinstalled; source and installed work/completion/phase suites each
passed 27 tests. All seven modified runtime modules matched installed wheel bytes.
The full 376-test runs precede this final lineage fix; they are not presented as
377-test final runs. Import paths were under site-packages; pip check, Ruff and
diff checks passed. Source and wheel logs are under
`/workspace/scratch/flora-handoff-round1-*`.

At this publication boundary all live and test processes have finished. No new
HTTP 401/402/403 or balance rejection occurred. Next work needs no renewed user
authorization. Continue with Pro phased routing and new-seed held-out cases, then
the authority of the existing original-task handoff: Coordinator already saves
parent_task/task_key and supplies parent_task in memory.data. Do not incorrectly
claim the original request is absent. A schema-4 task wrapper may preserve its
priority and visibility against parent-model reinterpretation; this remains a
design proposal, not an implemented or validated fix.

Still required: measured phase comparison and cross-model held-out tasks; faithful
delegation of original task constraints; spontaneous work decomposition/refinement;
Pro generation/latency; broader diagnostic insertion; repeated long-range recovery.
No universal success or causal-benefit conclusion follows from this round.
