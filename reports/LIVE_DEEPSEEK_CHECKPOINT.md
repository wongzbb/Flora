# Live DeepSeek reliability checkpoint

This is an intermediate checkpoint, not final acceptance. Runtime code: `d583a6a01fdd8bd561e8950c0fc50bbd466836f0`. Only `general-agent` is authorized for publication. Real requests use the user-confirmed `https://api.deepseek.com`, only `deepseek-flash` and `deepseek-v4-pro`. Credentials are never included in these reports.

## Recorded batches

| Batch | Passed / attempted | First-pass | p50 / p95 seconds |
|---|---:|---:|---:|
| flora-live-crossmodel-20261005 | 6/9 | 6 | 54.165 / 235.6 |
| flora-live-flash-baseline-20261001 | 2/4 | 2 | 41.0995 / 56.341 |
| flora-live-flash-direct-20261001 | 0/2 | 0 | 8.7475 / 16.231 |
| flora-live-flash-final-20261004 | 7/8 | 6 | 44.206 / 131.045 |
| flora-live-flash-heldout-20261002 | 7/8 | 6 | 76.2485 / 197.995 |
| flora-live-flash-schema-fixed-20261001 | 1/2 | 1 | 146.383 / 247.572 |
| flora-live-flash-transport-fixed-20261001 | 0/2 | 0 | 85.875 / 142.834 |
| flora-live-pro-baseline-20261001 | 0/1 | 0 | 120.09 / 120.09 |
| flora-live-pro-heldout-20261002 | 3/4 | 3 | 110.8345 / 452.402 |
| flora-live-pro-low-20261001 | 1/2 | 1 | 177.109 / 244.937 |

The crossmodel-20261005 batch is still running; its row count is a snapshot, not a final denominator. Individual failures remain in the JSON report. Per-case seeds, grades, operational configuration and mechanism counts are retained. Earlier dirty development batches lack exact source snapshots; do not treat them as reproducible clean-SHA comparisons. The new harness records source identity before requests.

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

The new release_audit combination covers two workers, conflicting/untrusted notices, a missing primary followed by its declared fallback, insufficient evidence retained as unknown, parent source checks and publication readback. Its oracle binds the failure/fallback order to the correct worker. The live combination is pending. A reusable --reopen-after-write mode can exercise saved-session continuation without injecting task answers.

## Verification and limits

Independent review found no checkpoint blocker; 50 focused tests passed. Exact-checkpoint source verification passed 301 tests (one platform skip) in 222.904 seconds. The wheel was built, installed with general extras into a separate virtual environment, and its import path verified; the same 301 tests passed (one platform skip) in 215.775 seconds. Ruff and pip check passed. The publication commit adds only these reports to the verified code SHA.

This is synthetic-family coverage. It does not establish arbitrary-task reliability, real external irreversible-action recovery, general research accuracy, live spontaneous diagnostic synthesis, accepted model contract revisions, or causal improvement. Reports and citations require separate publication checks.

## Cost and replay

Recorded aggregate parent + worker budgets, once per case. Unknown-usage calls and reserved output prevent exact billed token/currency claims. No verified currency bill available. A separate minimal connectivity completion used 9 input and 1 output token.

Aggregate recorded counters: `{"model_calls": 126, "tool_calls": 162, "input_tokens": 1283762, "output_tokens": 787824, "unknown_usage_calls": 2}`. These are budget records, not a verified monetary bill. Resume attempts are not double-counted. Latencies include bounded repair/recovery; the median and nearest-rank p95 are descriptive, not a controlled speed comparison.

Replay from the recorded code using `python -m tests.live_reliability_probe`, the user-confirmed base URL, explicit model IDs, the recorded seed/cases/rounds, and the non-sensitive deepseek-low profile. Keep evaluation output outside the repository. Never pass credentials on the command line; use the environment variable name. See the JSON for evidence directories and per-case values.
