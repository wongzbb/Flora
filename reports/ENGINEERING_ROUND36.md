# Engineering Round 36 — reconcile nested capability instructions

## Scope

A live five-level task under the previous code returned `nested_chain=not_possible` after executing only one child. The child was told that it could not delegate, even though the coordinator had exposed nested delegation tools at non-leaf depths. This was a semantic capability contradiction, not an output-format problem: the model was instructed to reject the required decomposition.

The generic read-only worker guidance now forbids mutation, commands and service mutations while allowing delegation when (and only when) a nested coordinator is actually exposed and the assigned task explicitly requires a separable nested subtask. Leaf workers still receive an explicit no-delegation instruction from the depth-bound branch. The authority and completion gates remain unchanged.

## Offline verification

- Relevant handoff, general, collaboration, kernel and coordinator recovery tests: **92 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.
- Added a regression check that the shared worker guidance does not contradict the nested capability it is used with.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway.

### Before this change

Run: `/private/tmp/flora-round36-dsv4flash-nested5`

DeepSeek v4 Flash interpreted the child guidance as a prohibition on recursive delegation. It executed one child, read and reviewed the numeric result, then returned a limitation saying a five-level chain was impossible. The completion gate was satisfied only because the model explicitly recorded that limitation; no nested chain was established.

### After this change

Run: `/private/tmp/flora-round36-dsv4flash-nested5-fixed`

The same five-level arithmetic task caused the model to create a sequential chain of five nested child identities (including the leaf), with nested contracts carrying the depth, numeric output guarantee, and required wait/read/review evidence. This confirms that the exposed capability and the model-facing contract now agree.

The run did **not** complete end to end. At approximately 632 seconds, the root encountered a `MISSING_KEY`/invalid wait-result branch while a child was still running and ended `needs_program` after bounded repair attempts. No value was promoted as success; completion remained unready and the unfinished child was retained. The change therefore proves improved delegation reachability, not general five-level reliability.

## Limitations and next target

- Waiting on a running nested child remains model-sensitive: the host supplies `result_available=false` and `result=null`, but the generated program still attempted an invalid observation path.
- Five-level completion, fan-out, mixed tool calls and other models remain unproven.
- The next improvement should address semantic phase branching around unavailable child results without fabricating an answer or adding a task-specific decomposition.
