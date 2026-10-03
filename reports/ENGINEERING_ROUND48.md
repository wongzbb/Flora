# Engineering Round 48 — multilingual contract type descriptors

## Scope

Round 47 used GLM-5 on the seven-child calculus task. The children declared
outputs such as `最终答案字符串`, `清晰的题目描述字符串`, and
`详细的解题步骤字符串`. The host could not classify those descriptors, so
all three fields were `contract_check=unknown`; repeated review could not
establish the guarantees and the parent exhausted its model-call budget.

This round adds a small, model-independent semantic vocabulary to the
contract type classifier. Chinese terms for null, boolean, number, string,
object, and array are recognized alongside the existing English vocabulary.
The change only improves interpretation of a declared contract. It does not
turn an absent field, an unobserved value, or a failed evidence check into a
pass, and it does not add a model-specific output format.

## Offline verification

- Coordinator recovery, general, collaboration, handoff, and kernel tests:
  **99 passed**.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and
  `git diff --check` passed.
- Added regression coverage for `最终答案字符串` and `输出为数值`.

## Live API validation and cross-model comparison

The user-provided OpenAI-compatible gateway was used with separate model
runs.

### GLM-5 comparison

Before the classifier change, `/private/tmp/flora-round47-glm-five-calculus`
started all seven children, but every declared Chinese string output was
unknown. The parent performed repeated reviews and stopped with
`budget_exhausted` after 12 model calls and about 339 seconds.

After the change, `/private/tmp/flora-round48-glm-seven-calculus-types` did
not reach child execution: GLM's generated program failed validation twice
(`process_read.term: argument keys must match 'read_next' parameters`) and
stopped as `needs_program` after two model calls. This is an independent
program-generation limitation; it supplies no evidence that the classifier
change makes GLM broadly reliable.

### DeepSeek-v4-flash validation

`/private/tmp/flora-round48-dsv4flash-contract-alias` used the same semantic
contract vocabulary on a five-child, one-tool-per-child task. All five
children were required, fully read to `next_offset=null`, and reviewed:

- 2 contract checks passed and were accepted;
- 3 real contract violations were blocked;
- the final limitations preserved type mismatches and the denied web request;
- no unresolved contract was accepted as success.

The run completed in about 102 seconds. This confirms that the aliases route
natural-language descriptors into the existing pass/violation gate without
weakening the evidence boundary. It is a targeted cross-model/output check,
not a broad success claim.

## Remaining limitations

- GLM program compilation and argument generation still fail on some complex
  prompts before any dual-control observation is available.
- Type vocabulary recognition covers common semantic terms, not arbitrary
  prose; unknown descriptors remain unresolved and cannot be accepted.
- Contract review checks collection, declared interfaces, and evidence
  linkage, but does not prove the factual truth of an accepted result.
- Seven-way calculus success after this change and deeper nested delegation
  remain unverified.
