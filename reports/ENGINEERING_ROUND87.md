# Engineering Round 87 — batched child resumption

Date: 2026-10-05  
Branch: `general-agent`  
Base: `8358ed3`

## Change

Added the generic `resume_agents(agent_ids)` host boundary. It resumes a
bounded set of unfinished children using each child's existing trace, budget,
contract and effect journal. It returns one observation per requested child,
including a typed error when that child cannot be resumed. It does not collect,
review or accept results. Coordinator guidance now recommends it after a
collection shows unfinished children, so recovery can be incremental instead
of regenerating a whole future workflow.

This preserves dual control: a resumed child continues from the actual
observed interruption or compiler failure, and its next effect produces a new
observation. It preserves synthesized contracts: the original contract and
receipt history remain attached, while review and completion gates still run
after collection.

## Verification

- Collaboration, coordinator recovery, structured compiler and streaming
  regression tests: **116 passed** in 8.22 s.
- The existing real GLM 5.3 simple task under the same live profile completed
  `2+2=4` in 4.79 s; this confirms the checked-out live configuration remains
  usable after the host change.
- A real GLM 5.3 task explicitly requesting batch resumption stopped before
  dispatch because the parent semantic plan exhausted two compilation attempts.
  `resume_agents` was therefore not called in that run. This is retained as
  negative evidence and is not presented as validation of the new boundary.

## Limit

The host boundary is now available to a model, but a model must still compile a
small recovery phase that selects the failed IDs. Long initial plans can fail
before that observation exists. The next real test should arrange an actual
interrupted child and verify resume, recollection and review separately; no
claim of broad multi-tool reliability is made here.
