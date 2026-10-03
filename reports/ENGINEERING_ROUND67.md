# Engineering Round 67 — GLM-5 exact-argument guidance comparison

The prompt was further clarified that every jump/branch/call/effect/observe argument object must exactly match target parameters and must not pass undeclared pagination/envelope fields.

Offline verification again passed 104 relevant tests plus ruff, compileall, and diff checks.

A third real GLM-5 run still did not execute tools. It produced several distinct compiler errors across 12 model calls (wrong error-target args, missing branch args, invalid get arity, undefined SSA variable) and was marked `stalled` for repeated compilation without new observation. No side effects were replayed or accepted.

The DeepSeek runs remain successful for three-level, five-level, seven-child, and five-child multi-tool tasks. GLM cross-model reliability remains an unresolved blocker; this evidence argues against claiming broad model-independent success.

