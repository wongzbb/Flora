# Tool schema compatibility and external evidence coverage

Continuation from `c61a4e8018fef7fb37b10849008d469c2cc80069`, 2026-10-01.

## Actual failures and attribution

The completed schema-4 Flash release audit failed after 390.988 seconds. Its
parent used 12 model calls and 13 tool calls; including workers, usage was 18
model calls, 16 tool calls, 380869 input tokens and 100213 output tokens.
The parent exhausted its model-call budget. Of seven compiler refusals, four
were the host's unsupported `pattern` schema keyword, two were observe target
parameter errors, and one was duplicate JSON key `inputs` in a complete 2220-byte
program (not truncation or a nonfinite number). These are separate failure classes.

The original-task handoff did not prevent semantic type drift. A worker received
the original task and the actual integer 182, then explicitly applied `to_string`.
The parent accepted the result and published a string. The worker also added
evidence fields rejected by the current exact worker-answer oracle; that separate
interface issue does not excuse the incorrect published type.

Read-only receipt inspection confirms full source reads by both workers and the
parent, one successful publication, and a full matching-hash readback afterward.
This failure is not merely missing evidence access. The 16 consumer checks prove
their declared local relations, not preservation of the user's intended type.

The prior Pro phased routing run completed in 832.115 seconds with 11 aggregate
model calls, 15 tools, 175407 input tokens and 87484 output tokens. Its answer and
worker results passed the then-current external oracle. Read-only retrospective
inspection confirms complete matching-hash source reads for parent and workers;
the same is true of the earlier Flash phased routing pass. This does not prove
semantic use of those sources. Pro generated 63904 program-text bytes in total,
with a largest generation of 16030 bytes: latency is not explained solely by one
large program. Several individual model responses took 117–149 seconds.

Neither routing run used diagnostic insertion, contract revision, or work-plan
refinement. Passing ordinary tasks does not establish benefit from these mechanisms.

## Schema repair

Schema 4 advertised `pattern` and `uniqueItems`, neither supported by the tool
validator's deliberately bounded JSON Schema subset. Valid update/refinement
requests therefore failed before their handlers. The advertised schema now uses
supported length bounds and explicitly describes the additional ledger-enforced
ID grammar and distinct-ID requirement. The existing ledger enforces both; neither
constraint has been removed from execution. Unknown schema keywords still fail
closed. Literal/dynamic static argument validation and real registry calls are
covered, including invalid IDs and duplicate superseded IDs without mutation.
An offline replay of all four actual schema-rejected model programs now passes
source lowering and static tool-argument validation with the repaired schema.
This replay makes no model or task-tool calls and does not claim the complete
programs would finish correctly; it isolates removal of the observed host blocker.

Tool schemas contribute to session identity. This repair changes schema-4
fingerprints; old schema-4 sessions are not silently resumed under different
schemas. New live tests must use new sessions. Legacy schema versions are unchanged.
Absence of observed refinement under the broken schema cannot be used as clean
evidence that a working refinement mechanism would be ignored by the model.

## External evidence oracle

Path contact remains useful for forbidden-access checks but is no longer treated
as sufficient source observation. The revised external oracle requires complete
coverage of the fixture's original version: file byte/line windows reconstruct
their hash; extracted documents bind source identity and raw-input hash; table
evidence distinguishes complete unfiltered rows from a task-relevant computation.
Actual large table results stored in paged sources are covered separately.

Publication checks report successful write counts independently. Final readback
must fully cover the last successful write's version, matching the final file hash.
Source observation must precede publication where the task requires that order;
fallback access cannot be justified by a later missing-primary observation.
Once-only publication is enforced only when explicitly required by the fixture.
Existing release-audit wording did not specify exactly once, so this change does
not silently add that requirement. Truncated/unrelated windows, cross-version
stitching, late source access and repeated publication have adversarial regressions.

These are structural observation checks, not proof of understanding or semantic
use. Mixed byte-window plus line-window coverage is conservatively unsupported
even when their union is complete; current GeneralAgent does not expose read_lines,
so this limitation does not alter the evaluated ordinary general-agent trajectory.
One XLSX worksheet is not treated as complete observation of the entire workbook.
These coverage limitations also appear in the machine-readable grade.
Strict worker answer matching is unchanged in this round.

## Final validation

Independent schema review and oracle review passed. The final source suite passed
411 tests with one platform skip in 198.448 seconds; the installed wheel passed
the same 411 tests with one skip in 198.951 seconds. Both complete runs include the
last optional-primary/no-match-search regression. No code/test edits followed.
The installed schema module matches final source bytes; pip check, Ruff and diff
checks passed. Logs: `/workspace/scratch/flora-round3-source-final.log`,
`flora-round3-wheel-final.log`, `flora-round3-build.log` and `flora-round3-install.log`.
The earlier 410-test runs preceded that final regression and are not substituted
for these final complete results.

## Delivery limits and next comparisons

The ordinary default remains schema 3 and the older compiler profile. This round
does not claim the opt-in path has become a reliable default. The independent
syntax-v4 experiment is excluded to avoid confounding the schema repair.
After matched heldout evidence supports a recommendation, publish a normal-use
profile without the experiment's provider settings and budgets. Only then consider
changing `_new_session_defaults` for new general-v4 sessions. Do not inject upgrades
into `_normalize` or `read_profile`: saved sessions already persist their resolved
profiles and reject mismatches. Terminal `/new` from an old session currently
inherits its profile; preserve and test that behavior. Default migration needs CLI,
terminal, Python API, worker inheritance and committed-effect recovery tests.

Next comparisons use matched task, model, tool grants and budgets. New fixture
seeds alone are not unfamiliar tasks. Outcome-only new families must test whether
the model chooses decomposition and synthesizes useful source-to-output checks,
including uncertainty, conflict and explicitly permitted type conversion. Keep
mechanism executability, natural use and measured task benefit separate.

Aggregate live evidence and retrospective receipt metadata are in
`ENGINEERING_ROUND3_EVIDENCE.json`. No new authentication or balance rejection
occurred. No universal reliability claim is made.
