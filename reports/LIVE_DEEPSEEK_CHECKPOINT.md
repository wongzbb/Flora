# Live DeepSeek reliability checkpoint

This is an intermediate checkpoint, not final acceptance. Initial verified runtime checkpoint: `d583a6a01fdd8bd561e8950c0fc50bbd466836f0`; follow-up runtime code: `4e29789`, included in closeout base `4182d453191b5c029287eebfe5600f9961975397`. Only `general-agent` is authorized for publication. Real requests use the user-confirmed `https://api.deepseek.com`, only `deepseek-flash` and `deepseek-v4-pro`. Credentials are never included in these reports.

## Recorded batches

| Batch | Passed / attempted | First-pass | p50 / p95 seconds |
|---|---:|---:|---:|
| flora-live-crossmodel-20261005 | 7/11 | 7 | 180.998 / 541.505 |
| flora-live-flash-baseline-20261001 | 2/4 | 2 | 41.0995 / 56.341 |
| flora-live-flash-direct-20261001 | 0/2 | 0 | 8.7475 / 16.231 |
| flora-live-flash-final-20261004 | 7/8 | 6 | 44.206 / 131.045 |
| flora-live-flash-heldout-20261002 | 7/8 | 6 | 76.2485 / 197.995 |
| flora-live-flash-schema-fixed-20261001 | 1/2 | 1 | 146.383 / 247.572 |
| flora-live-flash-transport-fixed-20261001 | 0/2 | 0 | 85.875 / 142.834 |
| flora-live-pro-baseline-20261001 | 0/1 | 0 | 120.09 / 120.09 |
| flora-live-pro-heldout-20261002 | 3/4 | 3 | 110.8345 / 452.402 |
| flora-live-pro-low-20261001 | 1/2 | 1 | 177.109 / 244.937 |

The crossmodel-20261005 batch finished with 7 passes, 3 task failures, 1 HTTP failure and 1 unrun trial. Trial 11 received HTTP 402; the global batch stop prevented trial 12 and cancelled the queued release_audit batch before it started. No further authenticated requests were sent. Individual failures remain in the JSON report. Per-case seeds, grades, operational configuration and mechanism counts are retained. Earlier dirty development batches lack exact source snapshots; do not treat them as reproducible clean-SHA comparisons. The new harness records source identity before requests.

## Findings and changes

- Stream wire overhead previously exhausted the same cap as decoded output. Independent wire/decoded limits now retain deadlines and strict completion checks.
- Collaboration argument schemas now advertise host-enforced bounds and are versioned for saved-session compatibility.
- Malformed source is rejected and repaired by the model with bounded lexical diagnostics; the host never rewrites program semantics.
- One Pro result quoted register references inside literal data. A bounded advisory now asks the model to review this valid IR; it may keep intentional data. It consumes the existing optional repair allowance and normal budget. Budget exhaustion stops compilation. It does not supply an answer, infer task semantics, or remove literal wrappers.
- Original pagination wording did not require a JSON number, while the oracle did. The prior correct decimal-string answer remains a recorded failure with this ambiguity. New wording explicitly requests numbers. Later failures were distinct: boolean filters did not match CSV strings. Comparison semantics remain unchanged.
- A later worker task returned its result window rather than the enclosed answer. This remains a real interface/termination failure, not a pass.
- Parent and child access-denial events stop later requests across the batch. Tests cover HTTP 401/402/403/404 with one and two worker slots. In-flight calls cannot be retracted. The reopen path rechecks denials after joining workers.

## Recovery and mechanism evidence

Both models completed an edit, paused after its successful mutation, closed/reopened the saved session and finished readback with exactly one successful write. Receipt prefixes and budget counters were preserved. This is orderly boundary recovery, not process-crash or unknown-commit recovery.

Natural live batches so far show consumer checks and actual source-dependent continuations, but no recorded diagnostic evaluation/insertion or contract revision. That does not demonstrate causal benefit or spontaneous synthesis. The observed bundles have not introduced multiple normal candidates, so the scheduler has had no demonstrated opportunity to choose between distinct candidate actions. This is a model-generation/coverage gap, not evidence that an eligible diagnostic was rejected. Ordinary observation followed by a pure branch is often sufficient for these fixtures. Offline mechanism tests retain diagnostic scheduling, differing continuations, revision checks and refusal of unsupported reuse; they are not substitutes for live-model evidence. New telemetry records actual diagnostic insertion, accepted revisions and rejected reuse separately.

The new release_audit combination covers two workers, conflicting/untrusted notices, a missing primary followed by its declared fallback, insufficient evidence retained as unknown, parent source checks and publication readback. Its oracle binds the failure/fallback order to the correct worker. The queued live combination was cancelled by the HTTP 402 access stop before execution. A reusable --reopen-after-write mode can exercise saved-session continuation without injecting task answers.

## Verification and limits

Independent review found no checkpoint blocker; 50 focused tests passed. Exact-checkpoint source verification passed 301 tests (one platform skip) in 222.904 seconds. The wheel was built, installed with general extras into a separate virtual environment, and its import path verified; the same 301 tests passed (one platform skip) in 215.775 seconds. Ruff and pip check passed. The publication commit adds only these reports to the verified code SHA.

This is synthetic-family coverage. It does not establish arbitrary-task reliability, real external irreversible-action recovery, general research accuracy, live spontaneous diagnostic synthesis, accepted model contract revisions, or causal improvement. Reports and citations require separate publication checks.

## Cost and replay

Recorded aggregate parent + worker budgets, once per case. Unknown-usage calls and reserved output prevent exact billed token/currency claims. No verified currency bill available. A separate minimal connectivity completion used 9 input and 1 output token.

Aggregate recorded counters: `{"model_calls": 140, "tool_calls": 185, "input_tokens": 1453871, "output_tokens": 941647, "unknown_usage_calls": 3}`. These are budget records, not a verified monetary bill. Resume attempts are not double-counted. Latencies include bounded repair/recovery; the median and nearest-rank p95 are descriptive, not a controlled speed comparison.

Replay from the recorded code using `python -m tests.live_reliability_probe`, the user-confirmed base URL, explicit model IDs, the recorded seed/cases/rounds, and the non-sensitive deepseek-low profile. Keep evaluation output outside the repository. Never pass credentials on the command line; use the environment variable name. See the JSON for evidence directories and per-case values.

## Access stop and follow-up code

The live acceptance remains incomplete. HTTP 402 is an account/payment-class rejection; no claim is made about its exact account-side cause. The failure is recorded in case 11, deepseek-v4-pro/dependency_route/trial 1. The requested cost allowance does not override this service rejection. Further paid calls remain stopped until access is restored and the stop is explicitly cleared.

Follow-up code 4e29789 includes schema version 3: complete worker windows carry an unverified structured result, partial windows still require collection, and table results disclose actual source cell types before filtering. Comparison and review semantics are unchanged; legacy version 1/2 sessions retain their descriptions and result shape. Independent review found no blocker. These changes have NOT yet passed live acceptance.

The mechanism investigation found that new user turns do not preserve previous candidates as revision targets; testing revisions must stay in the unfinished turn. Models also lacked a concise view of current and retained register structures. A bounded revision_state projection now exposes real locations/names/types, never values or nested data keys, and remains subject to normal context omission. A clearly labeled host-authored syntax example teaches how to propose migration; the model still chooses and writes the real program. Offline tests demonstrate historical/current EXTEND PASS without repeating the effect, and correctly reject PRESERVE for a fault-to-return change.

A separate legacy-consumer repair probe is prepared, explicitly labeled host-seeded initial program plus real model repair. It is neither end-to-end model generation nor spontaneous revision evidence, and was NOT executed after the HTTP 402 stop. No live diagnostic insertion, accepted model migration, or causal benefit is claimed. The successful task outcomes and offline gate tests must remain separate.

Follow-up verification: the full source suite passed 308 tests (one platform skip), followed by 43 passing focused checks for final operational-metadata changes. Ruff passed. The 4e29789 wheel built and installed successfully in a new virtual environment. Its import path was verified, and the full installed-package suite passed 308 tests (one platform skip) in 197.525 seconds.

## Exact saved error and measured usage

The saved main-actor model_failure event has category `http`, HTTP status `402`, and sanitized message `model HTTP 402: check the account balance`. The advice after the colon is generated locally from the status code. The provider deliberately does not retain a remote 402 body, headers or URL, so no exact upstream account error code/message is available. No new request was made to investigate it.

Across recorded evaluation batches and the two saved-session recovery runs: 140 model-call attempts, 137 with known usage; confirmed input 1,453,871 tokens and confirmed output 869,647 tokens. Three failed calls have unknown actual usage. The budget ledger's output total includes 72,000 reservation tokens for those failures; they are excluded from confirmed usage. A separate connectivity completion adds one call, 9 input and 1 output token. No dollar estimate is made.

For the final crossmodel batch alone: Flash made 16 calls with 149,988 input / 127,470 output tokens; Pro made 23 attempts with 249,608 confirmed input / 182,728 confirmed output tokens and one unknown-usage HTTP 402 call. The Pro ledger additionally charged a 24,000-token output reservation for that rejected call; this is not measured or billed usage evidence.

See LIVE_DEEPSEEK_RESUME.md for the explicit restart conditions, evidence inventory and rerun list. The overall live task remains incomplete.

## Closeout failure reconstruction

The Flash dependency_route trial-2 failure also lacked successful parent verification of the selected file. Both workers returned correct answers with complete accepted reviews and the required dependency. The parent successfully read route.json, then passed an empty path to read_file; it raised `ValidationError: A file path is required`. Read-only replay confirms only route.json in parent observed paths and a failed collaboration check. Version 3 exposes the structured worker result but does not automatically recover this failed read or choose the final answer. Both obligations still require live verification.

The CSV type/filter, structured-result and version-2 compatibility minimal checks were rerun successfully at closeout (3 tests). Installed-wheel pip check passed. No runtime code changed and no live requests were made during closeout. Session 20490 has retired; its previously captured successful terminal result is retained above, not a claim of a newly repeated full suite. The resume document contains the exact conditional commands and evidence inventory.
