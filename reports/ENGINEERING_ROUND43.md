# Engineering Round 43 — top-level contract type interpretation

## Scope

Round 41's five-child mixed-tool task was safely collected and reviewed, but all five children were blocked. One recurring cause was a contract such as `entries: array of objects with path and type`: the coordinator's substring-based type parser saw the nested word `object` first and classified the top-level output as an object, rejecting the actual array. This made a valid high-level interface look non-conforming.

The coordinator now resolves the top-level type from the beginning of a descriptor before examining nested words. `array of objects ...` is therefore an array; `object with fields ...` remains an object. The contract still checks required fields and dispositions, and does not infer or fabricate nested values. Guidance also clarifies that `contract.outputs` describes the child’s final returned `value`, while tool receipts, capability listings and status metadata are observations/evidence rather than outputs.

## Offline verification

- Relevant coordinator recovery, general, collaboration, handoff and kernel tests: **96 passed**.
- Added a regression for top-level array descriptors containing nested object words.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-flash`.

### Before this change

Run: `/private/tmp/flora-round41-dsv4flash-five-tools`

- Five children were spawned and each result was collected and reviewed.
- All five were marked `blocked`; no result was accepted. The trace included false shape violations such as an actual `entries` array being compared against a descriptor containing “array of objects”.
- The parent still completed honestly with limitations and `claims_verified=false`.

### After this change

Run: `/private/tmp/flora-round43-dsv4flash-five-tools-types`

- Five children were spawned, waited, read to `next_offset=null`, and individually reviewed in approximately 130 seconds with 5 model calls.
- Two children were accepted (`workspace_context`/`list_files` and `list_skills`), demonstrating that the top-level type fix removed at least one false contract rejection.
- Three children remained blocked for real reasons: network policy prevented web search, the capability result omitted declared fields, and no saved sources existed for the requested source evidence. These were reported as limitations and not promoted to success.
- The parent returned a complete synthesis with `completion_checks.ready=true`, while preserving `claims_verified=false` and the blocked-child limitations.

This is a cross-output improvement from 0/5 accepted children to 2/5 on the same class of mixed-tool task. It does not establish that all model-authored contracts are correct or that external claims are verified.

## Limitations

- The contract checker still validates only bounded top-level primitive types; nested object schemas are descriptive rather than structurally checked.
- Network-denied and unavailable-source cases remain correctly blocked; no fallback source was invented.
- Seven-way fan-out, five-level nesting with mixed tools, GLM/default-model behavior and side-effect tasks remain separate reliability targets.
