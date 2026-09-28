# SPDX-License-Identifier: Apache-2.0
"""General application defaults, distinct from the kernel and coding agent."""

from dataclasses import asdict

from flora.engine.budget import BudgetLimits
from flora.support.errors import ValidationError

UNLIMITED = dict.fromkeys(asdict(BudgetLimits()))


def unlimited_defaults(options=None):
    """Keep every explicitly supplied cap, disabling only unspecified limits.

    JSON null is the durable, exact representation of no limit. Zero remains
    a real zero budget. Infinity and large pretend-unlimited numbers are not used.
    """
    if options is not None and not isinstance(options, dict):
        raise ValidationError("budget must be an object")
    try:
        return asdict(BudgetLimits(**{**UNLIMITED, **(options or {})}))
    except TypeError:
        raise ValidationError("Unknown budget field") from None


def apply_defaults(profile):
    profile["budget"] = unlimited_defaults(profile.get("budget"))
    children = profile.get("general", {}).get("subagents")
    if children is not None:
        children["budget"] = unlimited_defaults(children.get("budget"))
