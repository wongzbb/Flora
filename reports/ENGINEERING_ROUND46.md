# Engineering Round 46 — unknown contract observations cannot pass review

## Scope

Round 45 exposed a contract gate hole. A child whose output check returned `status=unknown` (for example, an unavailable capability field) could still be passed to `review_agent` with `disposition=accepted`, because the host rejected only explicit `violation`. That let unresolved assumptions look accepted even though the dual-control observation had not established the guarantee.

The review gate now permits `accepted` only when the contract check is `pass` or `not_applicable`. `unknown` must be blocked/rejected or followed by an explicit contract revision. Contracted children are also required children, so `required=false` cannot bypass collection and review for a declared interface.

## Offline verification

- Relevant coordinator recovery, general, collaboration, handoff and kernel tests: **99 passed**.
- Added regressions for required contracted children and unknown output status handling.
- `ruff check src tests`, `python3 -m compileall -q src tests`, and `git diff --check` passed.

## Live API validation and cross-output comparison

Endpoint: user-provided OpenAI-compatible gateway. Model: `deepseek-v4-flash`.

### Before this change

Run: `/private/tmp/flora-round45-dsv4flash-required-contract`

- Five required children were collected and reviewed.
- The parent synthesis listed two `contract_check=unknown` children as accepted, even though their capability/source fields were not observed. This was inconsistent with the intended assume–guarantee meaning.

### After this change

Run: `/private/tmp/flora-round46-dsv4flash-unknown-contract`

- Five required children were fully read to `next_offset=null` and individually reviewed in approximately 135 seconds.
- Two children with `contract_check=pass` were accepted. Three children with real output violations were blocked. No unknown result was accepted; the final note explicitly records that all accepted entries passed and all unresolved entries were blocked.
- Completion remained ready only after all five had a review disposition, while `claims_verified=false` and limitations were preserved.

This is a cross-output behavior correction: unresolved contract observations moved from accepted in Round 45 to blocked or revised in Round 46. It does not turn blocked external/network/tool limitations into success.

## Limitations

- The live run did not produce an `unknown` status after the model revised its contracts; the gate is additionally covered by offline tests and the before/after output comparison.
- Contract checks still do not prove factual truth; review verifies collection, digest and declared interface integrity.
- Seven-way fan-out with structured contracts, mixed external evidence, GLM/default-model behavior and side-effect recovery remain to be tested.
