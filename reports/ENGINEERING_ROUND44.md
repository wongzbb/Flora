# Engineering Round 44 — structured assume–guarantee output checks

## Scope

Round 43 fixed top-level type interpretation, but its contract checker still treated nested object/array descriptions as prose. That meant a contract could declare a list without checking the element interface. This weakened the practical meaning of synthesizable contracts for multi-tool workers.

The coordinator now accepts bounded structured output descriptors with `type`, `items`, `properties` and `required`. It recursively checks declared array elements and required object fields, while preserving `unknown` for unsupported descriptors. String descriptors remain supported for compatibility. A mismatch is still a violation; no value is coerced or fabricated.

## Offline verification

- Relevant coordinator recovery, general, collaboration, handoff and kernel tests: **97 passed**.
- Added regressions for nested array/object item validation and missing required fields.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-flash`.

Run: `/private/tmp/flora-round44-dsv4flash-structured-contract`

Task: five children each use one to three tools; at least one child uses a structured array-of-object output contract. All children are waited for, read to `next_offset=null`, and reviewed.

- Completed in approximately 145 seconds with 5 children and 15 tool calls.
- The synthesis preserved two accepted children and three blocked children. The blocked cases included a nested structured mismatch (`services` returned an object but the contract required an array of objects), wrong primitive types, and a child program validation failure. These were retained as violations/limitations rather than accepted by coercion.
- The workspace/list result and source inventory passed their declared interfaces. `claims_verified=false` remained unchanged.

Cross-output comparison with Round 43 shows the structured path now reports element/field-level violations such as `artifacts[0].current is string, expected boolean`, rather than silently ignoring nested descriptors. It does not claim that the model's contracts or external observations are correct merely because the descriptor is well formed.

## Limitations

- Structured checking is intentionally bounded to the declared `type/items/properties/required` subset; unsupported schema vocabulary remains `unknown`.
- Network policy, unavailable tools and failed child programs still produce blocked results and require explicit limitations.
- Review and nested delegation gates remain separate from contract shape checks; a shape pass does not prove factual truth or nested completion.
- Seven-way fan-out, mixed tools with successful external evidence, and other models remain to be evaluated.
