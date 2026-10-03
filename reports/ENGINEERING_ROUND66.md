# Engineering Round 66 — GLM-5 observe-target guidance comparison

The prompt was updated with an explicit block-list-v3 example requiring both observe target blocks and their `params` arrays, while retaining strict semantic validation.

Offline verification passed 104 relevant tests plus ruff, compileall, and diff checks.

A second real GLM-5 three-level run no longer failed on the original observe-target error, but failed compilation on a different exact-interface error: a continuation passed `next_offset` to a target whose declared parameters were `agent_id,result_digest,v`. The host correctly rejected the program; no effects executed.

