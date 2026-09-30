# SPDX-License-Identifier: Apache-2.0
"""Typed, credential-free recovery advice; never authorizes replaying effects."""

from __future__ import annotations


def failure_info(status, reason=""):
    text = str(reason).lower()
    if status == "completed":
        return None
    if status == "interrupted_unknown":
        code, action = (
            "effect_outcome_unknown",
            "Resolve the journal with external evidence; do not replay the action.",
        )
    elif status == "budget_exhausted":
        code, action = (
            "explicit_limit",
            "A configured cumulative limit was reached; resume does not refund usage.",
        )
    elif "stale" in text and "anchor" in text:
        code, action = (
            "stale_program_anchor",
            "Compile against the current committed trace; never rewrite the proposed anchor.",
        )
    elif "strict json" in text or "validation" in text or "invalid bundle" in text:
        code, action = (
            "program_validation",
            "Repair only the program using the reported validation error and existing successful receipts.",
        )
    elif "transport" in text or "deadline" in text or "timeout" in text or "connection" in text:
        code, action = (
            "model_transport",
            "Check endpoint and model availability; a new compilation remains charged and may have unknown usage.",
        )
    elif status == "waiting":
        code, action = (
            "workers_pending",
            "Wait for the existing workers; do not create replacement duplicates.",
        )
    elif status == "yielded":
        code, action = (
            "scheduling_slice",
            "Resume the same journal and budget at the committed boundary.",
        )
    elif status == "stalled":
        code, action = (
            "no_progress",
            "Inspect the last actual error and change the strategy or clarify the missing input.",
        )
    elif status == "paused":
        code, action = "paused", "Resume the saved task at its next safe execution boundary."
    else:
        code, action = (
            "execution_incomplete",
            "Inspect the actual receipts and unresolved work before continuing.",
        )
    return {"code": code, "next_action": action, "effects_replayed": False}


def network_failure(exc):
    text = str(exc).lower()
    if "benchmark" in text or "198.18." in text:
        return "synthetic_dns", False
    if any(s in text for s in ("private", "reserved", "allowlist", "disabled")):
        return "network_policy", False
    if "credential" in text:
        return "missing_credential", True
    if any(
        s in text
        for s in (
            "429",
            "challenge",
            "captcha",
            "403",
            "502",
            "503",
            "deadline",
            "timeout",
            "resolve",
            "connect",
            "unavailable",
            "http read failed",
        )
    ):
        return "provider_unavailable", True
    return "invalid_observation", False
