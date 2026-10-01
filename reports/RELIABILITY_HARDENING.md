# Offline reliability hardening, awaiting live acceptance

Date: 2026-10-01 UTC

## Scope and baseline

This work starts from `general-agent` commit
`e0e6cb7afe7b896053eaed5112f425b0d367d782`. It retains the existing full Flora
kernel in the parent and each worker. No other branch is changed.

The unchanged baseline's regression suite ran 189 tests: 188 passed and one
macOS-only test was skipped on Linux. Passing that suite did not cover the fault
cases below and is not evidence of arbitrary-task or arbitrary-model success.

## Reproduced failures and changes

### Effects and receipts

A successful publication followed by a report/export artifact-receipt error could
be recorded as an ordinary raised tool outcome. Directory synchronization and
temporary-file cleanup could fail after text or binary publication in the same
way. HTTP response decoding and browser post-dispatch validation had similar
phase-classification gaps.

The adapters now distinguish pre-dispatch failures from post-publication or
post-dispatch observation failures. The latter produce `interrupted_unknown` and
halt. Fault-injection tests check the actual file/dispatch, durable reopen, and
absence of repeat dispatch. Source metadata needed by an export is read before
publishing. Unknown outcomes still require external evidence; this does not
provide exactly-once semantics for arbitrary services.

### Dependency scheduling and review freshness

A resumed dependent could occupy the only thread-pool slot while its prerequisite
waited behind it. Dependencies now wait as logical futures outside the executor;
only dependency-ready workers enter the pool. This does not implicitly restart a
failed prerequisite or dependent.

Rejected resumes leave collection/review state unchanged. Every review disposition
binds to the currently observed result and failure state. A worker with no answer
must still have its failure inspected before review. Handoff references are
rechecked before dispatch, and missing dependency results block honestly.

Submission failures, worker-finalizer failures, and failed registry writes while
settling paused/blocked workers must settle their logical futures and preserve
recoverability. Fault tests cover these cases and sibling progress.

An accepted review remains a model judgment. Empty evidence is allowed for pure
reasoning tasks, and reference checks do not prove that claims are true. Older
saved reviews lacking the new state digest conservatively require recollection
and review; existing tool descriptions and schemas remain compatible.

### Durable recovery accounting

The previous identical-error recovery allowance reset at each scheduling slice.
A one-step sliced/reopened run could therefore keep retrying the same failing
request indefinitely. Operational recovery state is now checkpointed separately
from model-owned memory. One recovery compilation and its explicitly proposed
retry remain possible; continued identical errors stop. A different requested
action remains available.

Pending or unknown outcomes do not count as changed evidence and do not refund
that allowance. Tests include a lost fourth settlement, reopen while unresolved,
external resolution to the same failure, and stopping with four calls and two
compilations.

### Long-task overhead and integrity

The journal keeps a bounded private verified reduction. Unchanged SQLite reads
check storage versions instead of rereading and rehashing the whole prefix.
External changes force full-prefix verification; they are not trusted as an
append-only suffix. Tests cover alias isolation, earlier-row tampering, concurrent
admission, exact settlement anchors, rollback, triggers, raw/canonical byte limits,
and external commits during snapshots.

Optional evidence retention finds the same valid newest suffix by binary search
instead of repeatedly validating and dropping one oldest record. Individual
invalid/oversized record treatment is unchanged. Byte, node and depth limits are
still checked. Real journals are never passed through optional evidence eviction.

## Evaluation and interpretation

`tests/live_reliability_probe.py` now separates:

1. Protocol/runtime completion
2. Independently checked task answers and output bytes
3. Evidence, mutation and publication integrity

Correct-looking answers without the required observations, incomplete or stale
worker collections, empty/wrong reports, changed input files, and publication
claims without matching actual output must not pass. Parent and worker usage is
aggregated once. Every attempt, skipped trial and bounded same-session recovery
is retained. Free-form research still requires human review.

Eight seeded stress families cover decimal joins, conflicting official notices
and injection decoys, paged tables, long documents, nested/null data, changing
schemas, exact edits with readback, and dependent file routing. Oracle keys live
outside each agent's authorized workspace. Seeds support paired implementations
and repeated held-out instances; these are synthetic families, not a public
benchmark or a claim of general capability.

Multiple candidates, diagnostic actions, forecasts, consumer checks and revisions
are reported as mechanism observations. Neither fixture success nor a count of
these events proves spontaneous synthesis, correct decomposition, or a causal
benefit from dual control and synthesizable contracts. No task grader is supplied
to the model or used as a hidden runtime completion decision.

For an explicitly authorized real evaluation, choose actual gateway-listed
DeepSeek and GLM IDs, provide credentials through hidden input or an explicitly
configured environment variable, and keep outputs outside the repository:

```bash
PYTHONPATH=src python -m tests.live_reliability_probe \
  --base-url https://YOUR_AUTHORIZED_ENDPOINT/v1 \
  --models deepseek-MODEL_ID,glm-MODEL_ID \
  --api-key-env FLORA_API_KEY \
  --cases stress --rounds 3 --seed 20261001 \
  --output /outside/repository/flora-evaluation
```

The probe rejects GPT/Claude and unrecognized family IDs, but labels do not prove
model provenance. HTTPS is the default requirement; insecure HTTP needs an
explicit opt-in. The probe never silently switches models. Operational per-case
bounds prevent runaway retries; token totals are not verified currency costs.

## Verification status

Final aggregate results are recorded in `RELIABILITY_HARDENING.json`.
The final source suite ran 278 tests: 277 passed, one platform skip, no failures
(170.872 seconds). The clean installed wheel passed all 89 focused new regressions
(10.693 seconds), and all changed runtime files match the installed copies byte-for-byte.
This checkpoint is offline hardening awaiting real DeepSeek/GLM acceptance.

- Baseline: 189 tests, 188 passed, one platform skip
- Final lint, compilation, whitespace checks, and dependency consistency checks passed
- Fault and recovery regressions include baseline-negative checks
- Clean virtual-environment installation with general document dependencies,
  dependency consistency check, installed CLI help, and installed calendar and
  pagination demos were exercised
- Independent adversarial review found additional scheduler and recovery edge
  cases during implementation; these received explicit regression tests
- Real authenticated DeepSeek/GLM trials have **not** run in this change session
  because secure endpoint/credential setup was unavailable
- No live-model success rate, production-readiness claim, or universal correctness
  claim is supported by these offline results

Local mechanism microbenchmarks are included in the JSON report. They preserve the
same observations and retained witnesses; they are not measurements of live-model
latency or broad task competence. The evaluator uses case-specific checks and a
conservative forbidden-search-scope check; it is not a proof about arbitrary
side effects or causal use of dependency results.
