# Engineering Round 97 — bounded replacement lineage

## Scope

Round 96 showed that semantic phase handoffs and nested shutdown snapshots
worked for successful runs, while a difficult GLM nested run kept replacing
budget-exhausted workers until the parent itself ran out of resources.  This
round adds a host-owned bound on that recovery path.  It does not choose task
decompositions or coerce child values.

## Changes

- `general.subagents.max_replacements` is a bounded option (default `3`,
  maximum `8`) exposed in interactive defaults and capability observations.
- Admission derives the replacement count from durable `replaces` lineage while
  holding the coordinator lock.  A restart cannot reset the count, a cycle is
  rejected as corrupt state, and a fourth replacement on the default lineage
  is refused with a limitation that the parent can report.
- Historical rows, their reviews, result digests and replacement observations
  remain intact.  An idempotent retry of an already-admitted replacement still
  returns the existing identity.

This is a resource and continuation invariant, not a task-specific failure
string patch.  A blocked/rejected observation may authorize a revised local
contract, but it cannot authorize unbounded repetition without new progress.
The replacement contract and evidence checks remain unchanged.

## Offline verification

- Focused collaboration, coordinator-recovery, terminal and replacement tests:
  **80 passed**.
- The new test admits one replacement, rejects that replacement, and verifies
  that a second replacement is refused when the configured lineage bound is
  `1`.
- Full suite and `compileall` are run before the final commit; results are
  recorded below after completion.

## Real API verification

All temporary runs used the user-authorized endpoint.  Credentials are omitted;
run directories are outside the repository.

### Four-layer nested task (before this round's cap, current Round 96 code)

The same task required one child at each of four levels, collection and review
at every boundary, and a leaf computation.

- DeepSeek: `/private/tmp/flora-round97-deepseek-nested4-lifecycle-20261006a`
  completed with `ready=true`.  The durable tree contained exactly one required
  child at each layer; layer 3 and layer 4 were collected and accepted, and the
  root projection carried nested observations.  The result explicitly kept
  `claims_verified=false` for the arithmetic claim.
- GLM: `/private/tmp/flora-round97-glm53-nested4-lifecycle-20261006a` ended
  `needs_program` after model connection interruption and resource exhaustion.
  It had reached layer 4, but several budget-exhausted replacements remained
  blocked and the root completion gate was false.  The persisted nested
  projection showed paused/unreviewed descendants rather than stale `running`
  descendants.  This is a failure, not a partial success claim.

The new lineage bound was not reached in these two runs; its invariant is
covered offline and will be exercised separately with a deterministic provider.

### Five-child, 1–3-tool task (current tree)

- GLM: `/private/tmp/flora-round98-glm53-5toolagents-bound-20261006a` ended
  `budget_exhausted` after provider-reported usage exceeded the parent budget.
  Five initial workers were admitted; several were budget-exhausted or
  rejected, and replacements remained unreviewed.
- DeepSeek: `/private/tmp/flora-round98-deepseek-5toolagents-bound-20261006a`
  ended `needs_program` after a model connection interruption.  Five initial
  workers were admitted; some completed and were accepted, while replacement
  reviews remained incomplete.

These failures show that endpoint reachability and child admission are not
completion.  They also show that the current test runner's parent/child caps
and provider interruptions remain material limits.  No broad reliability claim
is made from earlier successful five-child runs.

## Remaining limits

- The cap stops retry churn; it does not make a budget-exhausted child correct
  or make an unstable provider available.
- The default interactive profile has more generous child limits than the
  constrained real runner used above; both paths still need repeated holdout
  evaluation.
- Nested runs need further cross-model repeats and deterministic evidence that
  a lineage-limit refusal is surfaced as a parent limitation rather than a
  repeated model phase.
- `claims_verified=false` remains authoritative for child claims; accepted
  review and result digests establish collection/integrity state only.
