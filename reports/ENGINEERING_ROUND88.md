# Engineering Round 88 — semantic bundle surface and total guards

Date: 2026-10-05  
Branch: `general-agent`  
Base: `8358ed3`

## Change

The semantic compiler now accepts the complete semantic bundle surface instead
of reducing every response to one `main` program. Candidate programs retain
their IDs and inputs; semantic diagnostics retain their forecasts and
hypothetical witnesses; semantic revisions retain their target, migration and
mode. The host still lowers these plans to ordinary IR and applies the existing
anchor, tool, evidence and revision validators. This keeps contract synthesis
and dual control in the semantic phase rather than making them a formatting
only feature of the low level path.

The semantic language also treats an omitted `else` as an explicit empty false
branch. This is a general language rule, not a repair for a particular model
output. A bounded `resume_agents` host action remains available for independent
unfinished children and preserves each child's trace, budget, contract and
effect journal.

## Verification

- Focused compiler, structured planner, collaboration, recovery, revision and
  frontend regression: **141 passed** in 11.01 s.
- A full `tests/test_*.py` run reached **497 passed and 1 failed** after 25m38s.
  The only failure was the existing handoff-profile equality assertion, which
  assumed the authority and phased configurations differed only by tool schema
  version. The authority profile now intentionally contains `semantic_first`;
  the assertion was updated to account for that design change, and the focused
  post-change set passed.
- Real GLM 5.3 simple task (`2+2`) completed in 7.7 s with the semantic-first
  compiler. Real DeepSeek Flash `2+2` also completed, after one transport
  retry, in 75.2 s. These are cross-model smoke checks, not broad reliability
  evidence.
- Real GLM 5.3 seven-child calculus task created all seven children, collected
  all seven and submitted seven reviews, but failed while consuming the review
  result and then exhausted bounded semantic repairs. A preceding run failed
  earlier on a pure sequence-index error. Both runs are recorded as negative
  evidence; neither is called a pass.

## Limits and remaining work

The semantic bundle surface is now preserved end to end, but models can still
choose an invalid path through a valid tool result or emit malformed follow-up
JSON after a runtime observation. The host correctly refuses those programs;
it does not infer the intended result or silently coerce an absent field. The
seven-child task therefore remains unresolved for general GLM 5.3 behavior, as
does the five-worker multi-tool task and deeper nested delegation. The next
round should improve generic observation-to-consumer planning and recovery,
with a real nested task, rather than add model-specific output patches.
