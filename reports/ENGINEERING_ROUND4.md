# Outcome-only semantic transfer probes

Continuation from `e02dabd67f9a1cd90158012aed82e97c361c711e`, 2026-10-01.

## Same-budget schema-repair reproduction

Flash release_audit was repeated from clean e02dabd with the same fixture seed,
authority profile, bounded resume allowance and reopen-after-write option as the
c61a4e8 failure. It completed in 182.500 seconds. Aggregate usage was 10 model
calls, 17 tool calls, 147048 input tokens and 52160 output tokens. Parent-only
usage was 6 model calls and 14 tool calls. Unsupported-schema refusals dropped
from four to zero. One IR refusal remained: an effect contained observe-only
success/error fields. No effect was executed for that invalid program.

The final saved and returned answer had the correct number 182 and unknown/null.
One publication and complete matching-version readback were observed. The strict
collaboration grade still failed because the second worker added a notes field
to the required four-field answer. Independent read-only review isolated that
predicate without changing any saved result or digest: all other original
coverage, collection, review, fallback and preservation checks passed. The
recorded failure is retained rather than redefining the interface to obtain a pass.

No executable source-to-output relation was checked. Both workers observed files,
replanned and returned model-generated literals. Parent review and publication
were likewise generated after observation. Actual predicates checked tool status,
pagination and missing-file conditions, not date selection, type/value fidelity
or exact output fields. The 17 consumer checks do not certify those relations.
The root obligation was marked completed, with empty evidence, only after the
completion gate refused the first return. No natural subplan refinement occurred.

The numeric result cannot be causally attributed to schema repair: the prior
worker explicitly converted the same integer to a string; this run emitted the
numeric literal directly. This is one matched repetition, not a reliability rate.

## Healthy-interface Flash profile comparison

The schema-3 phased baseline was then run from the same clean e02dabd source,
with the identical fixture (395765991775520), seed 20261006, parent/child limits,
resume allowance and reopen option. Healthy interface means no unsupported-schema
bug, not a claim of successful task behavior.

| Profile | Seconds | Aggregate model/tools | Input/output tokens | Result |
| --- | ---: | --- | --- | --- |
| phased, schema 3 | 244.948 | 14 / 19 | 247170 / 65396 | Runtime completed; release was string "182"; task failed |
| authority, schema 4 | 182.500 | 10 / 17 | 147048 / 52160 | Runtime completed; final number correct; worker extra-field contract failed |

Both published once and fully read back the final version. Schema 3 had three
strict-JSON refusals and one effect/observe field error; schema 4 had one such
field error. All are actual events, not independent task samples. This is one
paired fixture per profile. Schema 4 simultaneously enables original-task
handoff, worker phase guidance and work refinement; the comparison cannot isolate
any one mechanism. Neither profile is established as a reliable default.
Independent baseline trace review found that the parent explicitly delegated
"release ... as a string". The worker read the actual numeric source and later
returned a string literal (no to_string operation in this run). Parent review
claimed agreement with the source without a type/value predicate. The baseline
root completion did contain the saved audit hash, after an attempted root-goal
rewrite was refused; that structural evidence did not detect the semantic error.

## New bounded task families

The independent outcome probe adds three small outcome-only families. It does
not prescribe worker counts, task decomposition, tool routes or checker programs:

- source_resolution: select official highest-rank records; preserve JSON types;
  distinguish confirmed null, absent evidence and conflicting highest-rank values.
- allowed_conversion: explicitly convert signed integer cents to two-decimal
  strings, preserving null. This rejects a blanket ban on numeric conversion.
- embedded_task: compute from records already in the user's task; no external
  source is needed, so evidence must not be universally forced.

The two source families support native JSON and a separately described CSV schema
with changed field names and JSON-encoded cells. Values and schemas vary separately.
The embedded family is identical across layouts and is explicitly marked as having
no schema shift; do not count it twice as independent transfer evidence.

The explicit arm adds only a request that the model generate and execute its own
local requirement/evidence-to-candidate check. It supplies neither checker code
nor expected answers. Natural tasks omit that sentence; compiler instructions
still expose Flora mechanisms. Neither arm's task pass proves a local check ran.
These short fixtures do not establish long-range recovery or arbitrary task ability.

## Execution and scoring boundaries

The driver loads a private instance of the existing reliability probe, reusing
its credential entrypoint, provider setup, bounded budgets, journals, usage,
cleanup and provenance. It does not modify the canonical imported probe. Each
invocation selects one authorized model, arm and layout, and at most three named
families, each attempted once without automatic resume. Endpoint is fixed to the
official DeepSeek HTTPS service. Nonfinite/unbounded evaluation budgets are
rejected without weakening finite profile values. Authentication/balance rejection
pauses the current work and marks subsequent families not_run.

Private expected answers remain outside the model's tool workspace. The external
oracle checks outcomes and unchanged files/directories; evidence comes from real
parent/worker receipts and the version-aware coverage oracle. Model-authored
citations are not accepted as evidence. JSON numbers compare numerically, while
booleans remain distinct. Reports retain actual/natural/explicit task hashes,
layout applicability and driver/fixture/support hashes. Check execution and causal
benefit still require separate trace review and appropriately labeled offline
counterfactual tests.

No runtime protocol, ordinary default or syntax-v4 change is included in this
round. Default migration remains conditional on matched, cross-model evidence.

## Validation and reproduction

Independent fixture/driver review passed, including additional child-event denial
checks. Final source discovery passed 428 tests with one platform skip in 197.885
seconds. The installed wheel passed the same 428 tests with one skip in 198.380
seconds. Runtime source is unchanged from e02dabd, so this uses the already built
round-3 wheel; new evaluation files are tested from the checkout against both
source and installed runtime. No code/test changes followed these complete runs.
Ruff and diff checks passed. Logs: `/workspace/scratch/flora-round4-source-full.log`
and `flora-round4-wheel-full.log`. Aggregate paired evidence is preserved in
`ENGINEERING_ROUND4_EVIDENCE.json`.

Example bounded invocation (output must be outside the repository):

```sh
PYTHONPATH=src python -m tests.live_outcome_probe --model deepseek-flash \
  --family source_resolution --family allowed_conversion --family embedded_task \
  --arm natural --layout json-v1 --seed 20261019 \
  --profile configs/deepseek-live-authority.json --api-key-env FLORA_API_KEY \
  --output /workspace/scratch/flora-outcome-natural-flash-01
```

At this code publication, the new driver has only offline validation; real
outcome-only results are not yet claimed. No authentication or balance denial
occurred in the completed Flash comparison.
