# Engineering Round 83 — contract evidence semantics

Date: 2026-10-04  
Branch: `general-agent`  
Base: `9674de6`

## Finding

The previous four-level run exposed a semantic contract dead end. Models put
requirements such as “collect the complete child result”, “use the review
digest”, and “return a number” into `evidence_requirements`. Flora correctly
treated free-form evidence strings as `UNKNOWN`, because it cannot prove them
from a file or source receipt. The model then tried to pass child values and
result digests as `review_agent.evidence`; the host correctly rejected those as
invented evidence. This produced repeated replanning and child budget exhaustion
even when the nested computation itself had completed.

## Change

The collaboration protocol now states the semantic boundary explicitly:

- `evidence_requirements` is only for host-checkable `file_read` or
  `source_read` observations, with actual path/source IDs and optional hashes.
- Collection, review, output type, and nested-completion obligations belong in
  `guarantees` or `dependencies`.
- Pure computations should use an empty evidence requirement list.
- `review_agent.evidence` accepts only actual `{source_id}` or
  `{path,sha256}` references. Child values, digests, statuses, and model claims
  remain observations, not evidence.

This is a contract meaning fix, not a model-format repair. Existing host gates
remain strict: textual evidence stays `UNKNOWN`, and a child claim cannot make
an unverified contract pass.

## Verification

- Focused collaboration/recovery regression: **82 passed**.
- Full offline suite: **488 passed** in 235.83 seconds.
- Real DeepSeek task (one child, pure `2+2`, no external source): **completed**
  in 81.5 seconds. The durable child contract had
  `evidence_requirements: []`; the final value was JSON numeric `4`, with host
  `contract_status: pass` and `disposition: accepted`. The model first attempted
  review while the child was still running; the observed host error changed the
  next program to collect again before review, demonstrating dual control.
- Cross-model GLM-4.5-Air control with the same task: three transport attempts
  ended in HTTP 502 from the test endpoint. No result was accepted; this is an
  upstream availability failure, not evidence of task success or semantic
  failure.

## Limits and remaining work

The change improves contract synthesis for pure computations and avoids a
known review dead end. It does not prove broad natural-language planning
reliability. The earlier four-level DeepSeek task still exhausted nested model
budgets after long recovery, although it no longer falsely accepted pending
children. More work is needed on bounded semantic phase planning and budget
allocation for deep nested tasks, with the same dual-control and contract gates.

