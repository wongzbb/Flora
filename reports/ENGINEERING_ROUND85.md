# Engineering Round 85 — compositional semantic plans and first-program progress

Date: 2026-10-05  
Branch: `general-agent`  
Base: `ddc58ff`

## Finding

The GLM 5.3 seven-worker task reached the collaboration host successfully,
but its first collection program passed numeric positions from spawn envelopes
where child IDs were required. The host rejected that action. The next model
phase used the actual error observation, collected all seven workers, and
reviewed each result. One digest was initially incomplete; a later collection
and review corrected that child before `complete_task` was accepted.

The five-worker, one-to-three-tools task exposed a separate boundary. Three
children completed and were reviewed, one child exhausted its own compiler
attempts without a result, and one returned values violating its declared
output types. The parent reported those failures and the completion gate
stayed closed. A second GLM 5.3 run stopped before any tool call because the
parent program exhausted its three compilation attempts. These are semantic
program-generation failures, not evidence that the endpoint or model ID is
unavailable.

## Change

1. `streaming._Progress` now treats `first_program_timeout` as the governing
   pre-program deadline when it is configured. The shorter idle-progress guard
   resumes only after the first meaningful program byte. Keepalives and
   reasoning therefore cannot postpone the first window, while a short idle
   window still detects a stalled response after a program has started.

2. The structured semantic compiler now lowers a map expression in any
   ordinary expression position, including nested tool arguments, conditions,
   loop sources, return values and replan state. It creates an explicit
   accumulator and bounded loop in the current lexical scope. If a mapped yield
   contains another map, the inner map is lowered inside the outer loop, so its
   bindings are available without leaking into the surrounding scope. This is
   a language normalization and does not inspect model names, JSON layout or
   error wording.

3. A nested error handler may use an explicit terminal-only plan; the host
   normalizes it to an empty `steps` phase. The handler still receives the real
   error observation and cannot silently continue after a failed effect.

These changes leave the dual-control boundary intact: an effect advances task
state and returns an observation, and the next program is compiled from that
observation. They also leave synthesized contracts intact: output types,
producer contracts, digests, review status and completion gates remain host
checks; no map lowering converts a value to a different type or treats a child
claim as proof.

## Verification

- Focused streaming and structured compiler tests: **74 passed** in 1.63 s.
- Full offline suite after the final change: **493 passed** in 6806.24 s. The
  unusually long run contained no test failures; future runs should isolate
  slow boundary tests from live API validation.
- Real GLM 5.3 seven-worker task: **completed** in 221.57 s, with 7 model
  calls and 7 tool calls. Seven distinct children produced question, solution
  and verification fields. The host collected and reviewed all seven before
  completion. The final result is `task_correctness=unverified`; no independent
  mathematical oracle was added.
- A final GLM 5.3 seven-worker run after the complete compiler change also
  **completed** in 92.14 s, with 4 model calls and 4 tool calls. The host
  collected seven child records, accepted all seven reviews and passed the
  durable completion gate. This confirms the final checked-out code can still
  execute the core collaboration path; it does not prove the harder
  multi-tool task.
- Real GLM 5.3 five-worker, one-to-three-tools task: **not completed**. In the
  first run, 3 children passed review, 1 was blocked by child compiler
  exhaustion, 1 was rejected for an output-contract type mismatch, and a later
  parent response hit `output_idle_timeout`; completion remained blocked. A
  fresh run exhausted three parent compiler attempts before dispatching a
  tool. Both failures are retained as negative evidence.
- Cross-output comparison remains available from the earlier DeepSeek run of
  the same collaboration family: the host accepted a two-worker batch after a
  type error and stale completion attempt were repaired. The DeepSeek and GLM
  traces have different planning paths, so this is not a controlled accuracy
  comparison.

## Limits and remaining work

The host now handles a broader class of semantic expression shapes, but it
cannot infer a missing decomposition or repair an arbitrary malformed contract.
Long GLM 5.3 programs still consume substantial reasoning budget and can fail
before their first effect. Child output correctness is not proved by contract
shape or review disposition alone. Deeper nested delegation, tool-side effects,
and resuming the blocked five-worker session still need independent real API
coverage. No claim of broad reliability is made from these runs.
