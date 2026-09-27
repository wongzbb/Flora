"""Deterministic scheduling over already-paused effects, not a reward model.

Scores are supplied structural diagnostic scores. Freezing another candidate is
never counted as information. Task/diagnostic quotas belong to the runtime.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from flora.support.errors import StaleAnchor, ValidationError
from flora.support.values import canonical_json


def _checked(frontiers: Sequence[dict]) -> list[dict]:
    if not isinstance(frontiers, (list, tuple)) or not frontiers:
        raise ValidationError("scheduler requires a nonempty frontier list")
    seen: set[str] = set()
    anchors: set[tuple[int, str]] = set()
    result = []
    for item in frontiers:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "request",
            "kind",
            "score",
            "anchor_epoch",
            "anchor_digest",
        }:
            raise ValidationError("frontier has missing or unknown fields")
        ident = item["id"]
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValidationError("frontier ids must be unique nonempty strings")
        seen.add(ident)
        if item["kind"] not in ("normal", "diagnostic"):
            raise ValidationError("invalid frontier kind")
        score = item["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
        ):
            raise ValidationError("frontier score must be a finite number")
        epoch, digest = item["anchor_epoch"], item["anchor_digest"]
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch < 0
            or not isinstance(digest, str)
        ):
            raise ValidationError("invalid frontier anchor")
        anchors.add((epoch, digest))
        request = item["request"]
        if not isinstance(request, dict) or set(request) != {"tool", "args"}:
            raise ValidationError("request must contain exactly tool and args")
        if (
            not isinstance(request["tool"], str)
            or not request["tool"]
            or not isinstance(request["args"], dict)
        ):
            raise ValidationError("invalid effect request")
        canonical_json(request)
        result.append(item)
    if len(anchors) != 1:
        raise StaleAnchor("cannot choose among frontiers from different real prefixes")
    if not any(item["kind"] == "normal" for item in result):
        raise ValidationError("scheduler requires a normal frontier for progress fallback")
    return result


def choose(frontiers: list[dict], incumbent: str, *, diagnostic_allowed: bool = True) -> str:
    """Choose an id without executing anything or estimating external utility.

    If all normal programs request the same effect, prefer the normal incumbent
    (or lexical normal id). Otherwise choose the highest positive diagnostic when
    permitted. Ties prefer diagnostics sharing a normal request, then lexical id.
    When a winning diagnostic requests an existing normal effect, return that
    normal id: one real action makes progress and supplies the same observation.
    With no eligible diagnostic, use the incumbent normal or lexical normal id.
    """
    checked = _checked(frontiers)
    normal = sorted((f for f in checked if f["kind"] == "normal"), key=lambda f: f["id"])
    by_normal_id = {f["id"]: f for f in normal}
    fallback = incumbent if incumbent in by_normal_id else normal[0]["id"]
    normal_requests: dict[str, list[str]] = {}
    for item in normal:
        normal_requests.setdefault(canonical_json(item["request"]), []).append(item["id"])
    if len(normal_requests) == 1 or not diagnostic_allowed:
        return fallback
    diagnostics = [f for f in checked if f["kind"] == "diagnostic" and f["score"] > 0]
    if not diagnostics:
        return fallback
    winner = min(
        diagnostics,
        key=lambda f: (
            -f["score"],
            0 if canonical_json(f["request"]) in normal_requests else 1,
            f["id"],
        ),
    )
    matching = normal_requests.get(canonical_json(winner["request"]), [])
    if matching:
        return incumbent if incumbent in matching else matching[0]
    return winner["id"]


__all__ = ["choose"]
