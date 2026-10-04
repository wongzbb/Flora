# Engineering Round 84 — batched collaboration boundaries

Date: 2026-10-05  
Branch: `general-agent`  
Base: `89748e2`

## Finding

The previous real seven-worker run completed, but the parent spent many model
compilations expressing mechanical wait, pagination and one-child-at-a-time
review control. A 330-second run used 10 parent/child model calls and 17 tool
calls. That control flow was exposed to model-generated shape mistakes even
though the required evidence and review gates were host responsibilities.

The same test endpoint also showed intermittent transport failures. Four
unprivileged DeepSeek attempts (using both supplied keys) and one GLM attempt
stopped before a program was created with `failure.code=model_transport`. They
created no child and had `effects_replayed=false`; these are provider
availability observations, not semantic task failures.

## Change

The collaboration host now exposes two generic, additive boundaries:

- `collect_completed_agents(agent_ids, timeout, limit)` waits once for a set of
  workers and mechanically collects every result to `next_offset=null`. Each
  returned envelope keeps its own status, full result and digest. It neither
  accepts nor reviews a child, and unavailable entries remain unavailable.
- `review_agents(reviews)` applies the existing `review_agent` check separately
  to every supplied child digest and evidence list. It persists one review per
  child and returns per-item host observations. A validation failure is returned
  as `status:error`, while `all_accepted` remains false; no failed item is
  silently accepted.

The instructions and boundary schemas recommend these operations for
independent workers while retaining the original single-child operations.
Contract checking, evidence rechecks, stale-digest rejection, nested-completion
gates and the final readiness gate are unchanged. The batch primitives therefore
move only deterministic scheduling and bookkeeping into the host; they do not
choose a decomposition or assert factual truth. An observed tool result still
changes the next model phase: in the live run a pure type fault caused a new
compilation before review and completion.

## Verification

- Focused collaboration/recovery tests: **84 passed** after the implementation.
- Full offline suite: **490 passed** in 985.34 seconds.
- Real DeepSeek task after the change, using the supplied endpoint through the
  authorized network path: **completed** in 158.16 seconds. It spawned two
  independent workers, called `collect_completed_agents`, then called
  `review_agents`. Both returned actual result digests with host
  `contract_status=pass` and `disposition=accepted`; the final task completion
  gate was ready. The run used 5 model calls and 5 tool calls and returned
  `sum_squares_1_100=338350` and `fibonacci_20=6765`.
- Cross-model control: `glm-4.5-air` against the same endpoint made three
  connection attempts and ended before program creation with
  `model_transport`. No result was accepted. This does not establish a GLM
  semantic failure.
- Earlier baseline evidence remains separate: the pre-change DeepSeek seven
  calculus-worker task and four-level nested task completed, but those runs do
  not validate the new batch API.

## Limits and remaining work

The batch API reduces protocol overhead only when the model selects it; old
single-child calls remain supported, so latency is not bounded for arbitrary
model plans. The live task still needed one compiler recovery, and model output
correctness was not independently proven by a mathematical oracle. The test
endpoint was unavailable for several unprivileged attempts and GLM remained
unavailable in this run. No claim is made that ordinary, deep nested, multi-tool
or all model-family tasks are now broadly reliable. More real tasks are needed,
especially nested tool delegation and side-effect uncertainty cases.
