# Engineering round 14 — contract observations and identity envelopes

## Scope

This round connects the handoff contract to an observed worker result. For the
declared output fields, the host checks JSON primitive types (`null`, boolean,
number, string, object, array). A completed result whose declared type is
wrong cannot be reviewed as `accepted`; the parent receives the mismatch and
can reject the result or revise the handoff. Unknown descriptors remain
unknown rather than being guessed. Bounded human-readable labels such as
`JSON number, not a string` map to the same generic primitive type; this is a
single contract-language boundary rule, not a model-specific output repair.

The same identity-envelope normalization now applies to `read_agent`,
`review_agent`, and `resume_agent`, as well as `wait_agents`. Only the
untrusted envelope's `agent_id` is consumed. This fixes a real failure where
workers had completed but the parent passed the complete spawn object to
`read_agent`, whose old schema accepted only a string.

These changes retain dual control: the parent observes spawn and wait results,
then reads and reviews the actual worker output; a contract observation can
change the acceptance branch. They retain synthesizable contracts: assumptions,
typed interfaces, guarantees, dependencies and evidence requirements remain in
the durable handoff and are not replaced by a format-only check.

## Real API validation

Using the replacement endpoint and credential with `deepseek-v4-flash`:

* the task spawned two workers, waited on their result envelopes, passed those
  envelopes through `read_agent`, reviewed both complete results, and returned
  `{42, 72, 114}`;
* a contract-generating run completed in 78.2 seconds with 3 model calls and 9
  tool calls; both child reviews recorded `contract_check.status = pass` for
  `derivation: string` and `result: number`;
* the durable completion gate still required the parent to update its required
  work step, and `claims_verified` remained false.

The same endpoint also produced two useful negative observations. A GLM-5 run
in the preceding paired comparison failed before effects because it referenced
an undefined variable. A separate DeepSeek run reached worker creation but
failed compiling an `observe` branch variant (`success`/`error` where the
current block expected `resume`). These are recorded as model/program
generation limitations, not hidden as contract successes.

## Offline validation

The collaboration, frontend and recovery suites passed: 63 tests. Ruff,
`compileall`, and `git diff --check` passed. Tests cover envelope handoff to
single-worker tools, contract preservation, primitive type violations blocking
accepted review, and bounded natural-language type aliases.

## Remaining limitations

The output checker only verifies declared primitive types; it does not infer
arithmetic truth, exact worker cardinality, evidence meaning, or task coverage.
`claims_verified` therefore remains false even when the type contract passes.
The compiler still has model-sensitive failures for some valid-looking
observe/block variants; future work should improve the language boundary
generically without accepting malformed programs or changing the semantics of
real tool outcomes. Nested depth and total-child bounds remain explicit
configuration controls, and nested cardinality contracts are not yet enforced
by the host.
