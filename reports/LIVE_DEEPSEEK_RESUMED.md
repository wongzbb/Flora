# Resumed live acceptance — in progress

The user confirmed recharge and explicitly cleared the HTTP 402 stop. One official `https://api.deepseek.com` / `deepseek-flash` completion succeeded: one request, 8 input tokens, 1 output token. This is separate from task-evaluation usage. No account operation was performed.

## Completed targeted batch

- Completed session: `22270` (exit 1); output: `/workspace/scratch/flora-live-v3-targeted-resume-01`.
- Source at batch start: `57392e5f31d054d2bb9268b9bcc5e2cdae15742b`; seed 20261005; pagination and dependency_route; both authorized DeepSeek models; two trials each; tool schema version 3.
- First completed row: Pro / pagination / trial 2 passed answer and evidence checks in 148.029 seconds. Two model calls, 18,059 input / 16,343 output tokens. One source-signature rejection was repaired within the existing budget. This is only one sample, not proof that the previous failures are fixed.
- Row 2 (Pro / dependency_route / trial 1) ended after 738.233 seconds with `reasoning_exhausted`: its last generation consumed the 24,000-token output allowance without a program. Both workers completed, but parent source verification and final answer did not. Aggregate: 9 calls, 108,344 input / 84,357 output tokens, no unknown usage. This is not an account rejection. The batch continues other eligible samples; do not restart it or repeat connectivity checks.

Latency is an acceptance issue of its own. Row 2's first four completed parent generations took 116.89, 47.69, 184.33 and 91.05 seconds. The third was rejected for a `get` operation with the wrong argument count. The second child also needed repair because its observe error target omitted capture parameters; this same source-signature issue occurred in row 1. These are completed streamed generations and compilation repairs, not merely idle API waiting. The final values are recorded above and in LIVE_DEEPSEEK_RESUMED.json. Do not treat eventual completion as sufficient usability evidence.

## Reviewed experimental harness

Commit `12b80c2` adds only `tests/live_mechanism_probe.py` and its offline tests. No runtime or core control/contract mechanism changed. Independent implementation review and a separate rerun of 10 offline tests passed; Ruff passed. The harness has not yet been run against a real model.

The probe explicitly asks the model to retain two source-grounded competing commitments and author its own diagnostic, forecasts, witnesses and consumer. All effects occur in a local dispatch simulation. Both possible actual states are exercised. The external post-run grade requires distinct normal effects, a positive structural score, actual diagnostic insertion, forecast feedback tied to the real receipt, a read before the only commit, and a correct final commitment. Ordinary observe-then-compute, declaration without insertion, or a wrong commit after a real diagnostic cannot pass the mechanism grade. No host-authored IR is supplied by the live harness. Offline tests use authored fixture responses and do not count as model evidence.

This is an elicited mechanism experiment. Even a live pass would not prove spontaneous synthesis or causal improvement over a normal conditional program.

## Remaining work

Finish the active eight-sample batch, preserving failures and investigating any recurring implementation cause. Then run the previously cancelled release_audit combination, both models' consumer recovery, and the reviewed diagnostic probe. Do not queue later batches blindly across an access rejection.

Consumer-recovery evidence needs careful separation: the existing host-seeded `not_a_real_field` fault leaves real historical checkpoints but no successful old behavior coverage. Accepted EXTEND with historical/current PASS on those checkpoints establishes a defined repair of failed behavior, not preservation of prior successful behavior. An accepted CHANGE or an unused library revision also does not establish the requested mechanism. Inspect mode, historical/current verdicts, activation and actual execution separately from task success.

If stronger revision evidence is needed, use a separate labeled legacy two-page fixture with an actual successful old-shape boundary followed by a real new-shape failure, and let the model write the entire migration/consumer. Keep any oracle external; do not introduce a task-specific host solver. No such new runtime feature or stronger live result is claimed here.

After each validated implementation round, commit and push only `general-agent`, verifying the remote ref and keeping incomplete experimental work explicitly labeled. Overall acceptance remains incomplete.

## Reasoning-profile investigation

The official [DeepSeek Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/) confirms `low` is the lowest enabled reasoning effort; `none` disables thinking, while `minimal` maps to `low`. There is no documented lower enabled setting to try. Any non-thinking comparison must explicitly disable thinking and use `none`, preserve task verification, use fresh bounded runs, and be reported as an experimental configuration. Earlier non-thinking Flash failures remain valid evidence; no speed or correctness improvement is assumed.

Separately, an isolated detached worktree is developing an opt-in source-syntax extension for explicitly declared capture subsets. It is not part of the active baseline or a verified runtime fix at this checkpoint.

## Final targeted outcome and next bounded comparison

Session 22270 completed: 4 passes, 1 task failure, 1 reasoning-exhaustion failure, and 2 unrun Pro trials skipped after transport-class exhaustion. Flash pagination trials 1 and 2 passed; Flash dependency_route trial 2 passed all worker/source/answer checks. Flash dependency_route trial 1 returned the route object after a single read, omitting workers and independent selected-file verification. This is a task-obligation/early-return failure, not addressed by capture projection or a structured worker window.

The next experiment uses `configs/deepseek-live-nonthinking.json` on Pro dependency_route trial 1 with the same seed. Only thinking mode/effort changes relative to the baseline; source syntax stays block-list-v2, tool schema remains version 3, budgets and external grading stay unchanged. The configuration parses and normalizes offline. It is experimental, not a new production default or a claimed fix.
