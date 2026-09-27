"""Only this module invokes registered capabilities; no automatic retries."""

from __future__ import annotations

import threading

from flora.engine.budget import Budget
from flora.integrations.tools import ToolRegistry
from flora.state.trace import MemoryTrace
from flora.support.errors import BudgetExceeded, InterruptedEffect, ValidationError
from flora.support.resources import ResourceLimitExceeded, validate_limit
from flora.support.values import canonical_json, clone


class EffectExecutor:
    def __init__(
        self,
        tools: ToolRegistry,
        trace: MemoryTrace,
        budget: Budget,
        *,
        max_output_bytes: int = 4_194_304,
    ) -> None:
        self.tools, self.trace, self.budget = tools, trace, budget
        self.max_output_bytes = validate_limit(max_output_bytes, "max_output_bytes", minimum=1)
        self._lock = threading.RLock()

    def execute(
        self,
        request: dict,
        *,
        epoch: int,
        trace_digest: str,
        mode: str = "observed",
        before_dispatch=None,
    ) -> dict:
        if mode != "observed":
            raise ValidationError("Hypothetical/replay machines cannot commit external effects")
        self.tools.validate_request(request)
        with self._lock:
            self.budget.check_time()
            # Admission occurs before journaling; stale anchors must not spend tool budget.
            if self.trace.epoch != epoch or self.trace.digest != trace_digest:
                from flora.support.errors import StaleAnchor

                raise StaleAnchor("Effect frontier has a stale anchor")
            if any(r["status"] in {"pending", "interrupted_unknown"} for r in self.trace.records):
                raise InterruptedEffect("Resolve the unknown external outcome before continuing")
            self.trace.reserve_effect(
                request["tool"], request["args"], max_output_bytes=self.max_output_bytes
            )
            self.budget.before_tool_call()
            event_id = self.trace.begin(
                request["tool"], request["args"], expected_epoch=epoch, expected_digest=trace_digest
            )
            if before_dispatch is not None:
                # Checkpoint failures belong to the host, not the tool outcome.
                # Leave the durable pending admission unresolved and stop.
                before_dispatch(event_id)
            try:
                value = self.tools.call(request)
                try:
                    value = self.tools.encode_result(value)
                    encoded = canonical_json(value)
                    if len(encoded.encode("utf-8")) > self.max_output_bytes:
                        raise ValidationError(
                            "Tool result exceeds configured observation byte limit"
                        )
                    outcome = {"status": "returned", "value": clone(value)}
                    self.trace.check_settlement_capacity(event_id, outcome)
                except (
                    ValidationError,
                    ResourceLimitExceeded,
                    ValueError,
                    TypeError,
                    OverflowError,
                    RecursionError,
                    MemoryError,
                ):
                    # Action happened, but a faithful serializable observation is unavailable.
                    outcome = {
                        "status": "interrupted_unknown",
                        "error": {
                            "type": "ObservationUnavailable",
                            "message": "Tool completed but its result could not be faithfully stored within configured limits",
                        },
                    }
            except BudgetExceeded:
                self.trace.settle(
                    event_id,
                    {
                        "status": "interrupted_unknown",
                        "error": {
                            "type": "BudgetExceeded",
                            "message": "Host budget stopped execution; effects may have occurred",
                        },
                    },
                )
                raise
            except (TimeoutError, InterruptedEffect) as exc:
                outcome = {
                    "status": "interrupted_unknown",
                    "error": {"type": type(exc).__name__[:128], "message": str(exc)[:2048]},
                }
            except (KeyboardInterrupt, SystemExit):
                self.trace.settle(
                    event_id,
                    {
                        "status": "interrupted_unknown",
                        "error": {
                            "type": "ProcessInterrupted",
                            "message": "Host interrupted during tool execution; effects may have occurred",
                        },
                    },
                )
                raise
            except Exception as exc:
                outcome = {
                    "status": "raised",
                    "error": {"type": type(exc).__name__[:128], "message": str(exc)[:2048]},
                }
            return self.trace.settle(event_id, outcome)
