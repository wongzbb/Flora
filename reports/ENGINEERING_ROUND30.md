# Engineering Round 30 — nested phase guidance and safe wait/read semantics

## Scope

This round follows two live failures in deeper delegation. A five-level DeepSeek chain spent a large child budget compiling an entire downstream workflow at once, then repeatedly encountered malformed or overlarge programs. A subsequent run reached four nested workers, but the parent attempted to read fields from a wait view before establishing that a result existed and hit `MISSING_KEY`.

The nested-worker guidance now requires one bounded phase per handoff or collection and tells the worker to replan from the observed child result before the next phase. Tool descriptions now document the actual `wait_agents`, `read_agents`, `read_agent` and `agent_status` envelopes: `result_available` gates access to result fields, pending results use `result:null` and an empty digest, and pagination/review remain separate obligations. This is semantic execution guidance, not a field-renaming compatibility path.

## Offline verification

- `PYTHONPATH=src python3 -m unittest tests.test_general tests.test_collaboration tests.test_recovery tests.test_coordinator_recovery tests.test_handoff_authority tests.test_projected_observe tests.test_frontend -q`
- Result: **126 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live evaluations

The user-provided endpoint was reachable in the permitted network environment. Model calls used the existing bounded runner and no network or side-effect tools in the task prompts.

### DeepSeek `deepseek-v4-flash`: three-level nested chain — passed

Run: `/private/tmp/flora-round30-deepseek-nested3`

- Root → middle → leaf chain completed; leaf returned numeric `7`.
- The parent performed waits, complete reads and reviews. Final status was `completed`, with 4 model calls and 7 tool calls.
- No `MISSING_KEY` occurred in the wait/read path. `claims_verified=false` remains correct because this was a computation without external factual evidence.

### GLM-5: same three-level chain — rejected safely

Run: `/private/tmp/flora-round30-glm-nested3`

- GLM-5 dispatched one child and waited, but later treated the stable child identity envelope as if it were a nested result object. The pure consumer failed with `MISSING_KEY`; two repair attempts then produced an unknown target `err`.
- The host returned `needs_program` and did not accept the child or invent a result. This is a cross-model output comparison, not a transport outage.

### Five-level chain stress evidence

- `/private/tmp/flora-round28-deepseek-fivelevel`: reached only depth two; the second-level child exhausted its six-call budget after malformed long programs. The parent retained the failure and marked the unresolved chain blocked.
- `/private/tmp/flora-round29-deepseek-fivelevel`: phase guidance reached four child levels, but the parent still failed on an unsafe wait-view field access and ended `needs_program` after `MISSING_KEY`.

These runs show progress in phase sizing and a successful three-level path, while also demonstrating that arbitrary five-level reliability is not established.

## Limitations

- Stable identity envelopes are accepted only at the tool boundary; models can still dereference envelope internals incorrectly. The host rejects the resulting program rather than changing the meaning of the child result.
- Five-level nested tasks remain budget- and compiler-sensitive. The new phase guidance reduces oversized programs but does not guarantee valid programs from every model.
- No external factual claims were established; `claims_verified=false` is preserved.
- Larger fan-out, side-effect recovery and mixed nested/tool tasks still need repeated live evaluation.
