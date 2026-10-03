# Engineering Round 68 — GLM-5 cross-output syntax comparison

This round compared the same three-level nested-contract task under the existing `block-list-v2` syntax, without changing repository code for the comparison.

The current branch's generic prompt clarification (explicit observe target params and exact cross-block argument matching) passed 104 relevant offline tests plus ruff, compileall and diff checks.

Real GLM-5 behavior under block-list-v2:

- The model spawned and waited for an L2 child.
- It repeatedly produced an observe error target with missing capture/bind parameters.
- After repair it attempted to review an unfinished child, then misused a child identity envelope and triggered the host's explicit envelope diagnostic.
- The run ended in compiler validation failure; no side effect was accepted as completed.

Compared with the block-list-v3 runs, the failure shape changed but did not disappear. This is evidence that the current issue is cross-model program/interface adherence, not one syntax spelling. The host correctly rejected invalid programs and unfinished children; no semantic or evidence checks were relaxed.

No broad cross-model reliability claim is made. Further work should target a principled model-independent execution/repair strategy or improve the underlying model capability, rather than accumulating syntax-specific patches.

