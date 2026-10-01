# Resume after the HTTP 402 stop

Status: live acceptance is incomplete. The user confirmed recharge and explicitly cleared the access stop on 2026-10-01; a single official Flash completion succeeded (8 input / 1 output token). The targeted v3 batch has resumed. See LIVE_DEEPSEEK_RESUMED.md for current progress. The remaining instructions below preserve the original conditional restart procedure; do not duplicate an active batch. No payment, recharge, account change or automatic retry is authorized by this document. Authentication previously succeeded; do not restart credential discovery or repeat initialization checks.

## Checkpoint and completed verification

- Before this documentation update, local and remote `general-agent` both resolved to `4182d453191b5c029287eebfe5600f9961975397`. Runtime follow-up code is `4e29789`; the final cross-model batch ran `7ebfc3c6250d07c35553bc0203c74bc0fcdadb3c`, before the follow-up fixes.
- Full source suite: 308 tests run, one skipped, successful; 200.610 seconds. A further 43 focused checks passed after operational-metadata changes.
- Installed wheel from `4e29789`: 308 tests run, one skipped, successful; 197.525 seconds. This terminal result was captured before the session was retired. Session 20490 is no longer attachable; it is not still running. The fresh environment's import path was verified. A subsequent `pip check` passed.
- Three focused offline checks were rerun at closeout: CSV cell types/exact filtering, structured complete worker result without automatic verification, and saved version-2 compatibility. All passed. No runtime change was needed during closeout.
- Sessions 37499 and 20490 ended. The queued session 34904 was cancelled before its release-audit run. No Python test process remained at closeout. No live request was made during closeout.

## Exact blocker and usage

The saved case-0011 main-actor event reports category `http`, status `402`, message `model HTTP 402: check the account balance`. The message is locally generated in `src/flora/integrations/providers.py`; remote 402 bodies and headers are deliberately not retained. The precise upstream account error code/message is therefore unavailable. The affected model was `deepseek-v4-pro`, `dependency_route`, trial 1. Do not assert a more specific account-side cause from this evidence.

The final cross-model batch has 7 passes, 3 task failures, 1 HTTP failure and 1 unrun trial. Its 39 model-call attempts comprise 38 calls with known usage and one with unknown usage: 399,596 confirmed input tokens and 310,198 confirmed output tokens. Flash: 16 calls, 149,988 input / 127,470 output; Pro: 23 attempts, 249,608 input / 182,728 output, one unknown-usage call. Pro's additional 24,000 ledger output tokens are a reservation, not measured usage.

Across stored evaluation batches plus two saved-session recovery runs: 140 attempts, 137 with known usage, 1,453,871 confirmed input / 869,647 confirmed output, three unknown-usage calls. A separate connectivity completion adds one call and 9 input / 1 output. No currency estimate is available. Preserve the difference between measured usage and budget reservations.

## Three task failures and fix coverage

| Existing failure | Saved evidence | Coverage in 4182d45 | Still required |
|---|---|---|---|
| Flash pagination trial 1 | Correct row count and signed sum, empty flagged IDs after boolean `true` filter against CSV string cells | Version 3 exposes actual `cell_types` before filtering; offline test confirms boolean does not match string and string does | Live model must choose the correct filter and return all required IDs |
| Pro pagination trial 1 | Same boolean/string mismatch | Same interface clarification; comparison semantics unchanged | Independent live retest; old failure remains a failure |
| Flash dependency_route trial 2 | Both workers returned correct answers, were fully collected and accepted, with the correct dependency. Parent read route.json, then called read_file with an empty path; it raised ValidationError, `A file path is required`. Parent returned the read_agent window instead of the answer | Version 3 adds an unverified structured result to complete windows. It does not automatically unwrap the answer or recover the failed parent read | Verify correct answer extraction **and** successful parent reads of route.json and the selected file; no claim that the empty-path failure is fixed |

The third failure was reconstructed read-only from saved child results/registry and SQLite receipts: worker review checks passed; parent observed paths contained only route.json; collaboration checks failed. This is independent of the wrong final result shape. Do not weaken the evidence oracle, substitute host answers, coerce CSV semantics or relabel old failures as passes.

## Evidence inventory

- `/workspace/scratch/flora-live-crossmodel-20261005/evaluation.json` and `provenance.json`: completed batch, per-model usage, seeds, grades, source identity and effective configuration.
- `case-0001` and `case-0002`: pagination failures; `case-0005`: worker-window and missing parent-read failure; `case-0011`: HTTP 402 transcript in `session/observations.sqlite3`.
- Per-case `oracle.json` is an external grading fixture, never model input. Read journals with SQLite `mode=ro`; do not reopen old sessions merely to inspect evidence.
- `/workspace/scratch/flora-live-reopen-20261003`: both models passed orderly write/pause/close/reopen/readback. This does not establish crash recovery or unknown-commit recovery.
- `/workspace/scratch/flora-wheel-4e29789/flora_lang-0.1.0-py3-none-any.whl` and `/workspace/scratch/flora-wheel-venv-4e29789`: tested wheel and installation. Scratch evidence is local; sanitized summaries are tracked in `LIVE_DEEPSEEK_CHECKPOINT.json`.

## Commands after explicit restoration

Use only the already confirmed official endpoint and the two explicit DeepSeek model IDs below. GLM is cancelled. Keep new output directories outside the repository, and use fresh unused directory names for every run. These commands are documentation only; none ran during closeout.

From `/workspace/Flora`, first verify `general-agent` and record its exact SHA. The profile below contains no credentials and deliberately leaves model and endpoint to the CLI. It retains the established low-reasoning budgets and explicitly states transport bounds. New sessions receive tool schema version 3; old saved sessions retain their prior version and are unsuitable for evaluating the new tool interface.

```bash
git branch --show-current
git rev-parse HEAD
PYTHONPATH=src /workspace/scratch/flora-preflight-venv/bin/python -m tests.live_reliability_probe \
  --base-url https://api.deepseek.com --models deepseek-flash,deepseek-v4-pro \
  --api-key-env FLORA_API_KEY --profile configs/deepseek-live-acceptance.json \
  --cases pagination,dependency_route --rounds 2 --seed 20261005 --resume-attempts 1 \
  --output /workspace/scratch/flora-live-v3-targeted-resume-01
```

This targeted run reproduces case/trial fixture seeds from the prior batch (the seed derives from batch seed, trial and case, independently of model/order). It covers the old failures and the interrupted/unrun cases without rerunning the entire completed batch. Inspect grades and receipts, not runtime completion alone. Stop on any access rejection; do not add `--continue-on-transport-error`. Review each batch before manually starting the next; do not queue subsequent calls in a shell loop.

Only after the targeted batch succeeds and access remains available, run the previously cancelled combination:

```bash
PYTHONPATH=src /workspace/scratch/flora-preflight-venv/bin/python -m tests.live_reliability_probe \
  --base-url https://api.deepseek.com --models deepseek-flash,deepseek-v4-pro \
  --api-key-env FLORA_API_KEY --profile configs/deepseek-live-acceptance.json \
  --cases release_audit --rounds 1 --seed 20261006 --resume-attempts 2 --reopen-after-write \
  --output /workspace/scratch/flora-live-v3-release-audit-resume-01
```

Separately test the prepared consumer-repair probe, one model at a time with inspection between runs:

```bash
PYTHONPATH=src /workspace/scratch/flora-preflight-venv/bin/python -m tests.live_consumer_recovery_probe \
  --base-url https://api.deepseek.com --model deepseek-flash \
  --api-key-env FLORA_API_KEY --profile configs/deepseek-live-acceptance.json --rounds 1 \
  --output /workspace/scratch/flora-live-consumer-flash-resume-01
```

Repeat that command for `deepseek-v4-pro` with a separate output directory only after inspecting the Flash run and confirming no access stop. This probe starts from a **host-seeded legacy fault**, followed by real model repair. It does not demonstrate end-to-end model-generated initial programs or spontaneous revision. Inspect actual revision mode, accepted/rejected migration and historical/current gate verdicts; task success alone is insufficient.

Natural diagnostic insertion, accepted model-generated revisions and causal improvement remain unverified. Offline EXTEND/PRESERVE tests and revision-state exposure are separate evidence. Resume investigation within the unfinished turn: a new user turn does not retain old candidates as revision targets. Do not add speculative mechanisms while blocked.

After each live stage, retain old failures, record source/configuration/seeds and confirmed versus unknown usage, and update acceptance claims conservatively. Publish any authorized changes only with `git push origin HEAD:refs/heads/general-agent`; never force-push or update another ref. Overall completion requires the remaining live evidence, not just account restoration.
