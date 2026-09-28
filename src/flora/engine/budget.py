"""Shared, thread-safe resource accounting; unknown provider usage is explicit."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from flora.support.errors import BudgetExceeded, ValidationError


@dataclass(frozen=True)
class BudgetLimits:
    """None disables an individual cap; finite kernel defaults remain unchanged."""

    max_tool_calls: int | None = 200
    max_model_calls: int | None = 30
    max_input_tokens: int | None = 200_000
    max_output_tokens: int | None = 100_000
    max_wall_seconds: float | None = 3600.0

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValidationError(f"Invalid budget limit: {key}")
            if key != "max_wall_seconds" and not isinstance(value, int):
                raise ValidationError(f"Budget count must be integer: {key}")
        if self.max_wall_seconds is not None and not self.max_wall_seconds < float("inf"):
            raise ValidationError("Wall time must be finite")


class Budget:
    """One ledger shared by tools, initial compilation, repair and recompile.

    Output is reserved before a model call. Missing usage consumes the full
    reservation and increments unknown_usage_calls. Input usage can only be
    checked after a response without a provider-specific tokenizer; it is not
    advertised as an exact admission-time cap.
    """

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self.limits = limits or BudgetLimits()
        self.tool_calls = self.model_calls = 0
        self.input_tokens = self.output_tokens = self.unknown_usage_calls = 0
        self._reserved_output = 0
        self._active_reservation: int | None = None
        self._lock = threading.RLock()
        self._started = time.monotonic()
        self._elapsed = 0.0

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed + time.monotonic() - self._started

    def check_time(self) -> None:
        if (
            self.limits.max_wall_seconds is not None
            and self.elapsed_seconds >= self.limits.max_wall_seconds
        ):
            raise BudgetExceeded("Wall-time budget exhausted")

    def before_tool_call(self) -> None:
        with self._lock:
            self.check_time()
            if (
                self.limits.max_tool_calls is not None
                and self.tool_calls >= self.limits.max_tool_calls
            ):
                raise BudgetExceeded("Tool-call budget exhausted")
            self.tool_calls += 1

    def before_model_call(self, info: dict[str, Any]) -> None:
        amount = info.get("max_output_tokens", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ValidationError("A positive output reservation is required")
        with self._lock:
            self.check_time()
            if self._active_reservation is not None:
                raise BudgetExceeded(
                    "Concurrent model calls require separate reservations; this compiler is sequential"
                )
            if (
                self.limits.max_model_calls is not None
                and self.model_calls >= self.limits.max_model_calls
            ):
                raise BudgetExceeded("Model-call budget exhausted")
            if (
                self.limits.max_input_tokens is not None
                and self.input_tokens >= self.limits.max_input_tokens
            ):
                raise BudgetExceeded("Input-token budget exhausted")
            if (
                self.limits.max_output_tokens is not None
                and self.output_tokens + amount > self.limits.max_output_tokens
            ):
                raise BudgetExceeded("Insufficient output-token reservation")
            self.model_calls += 1
            self._active_reservation = amount
            self._reserved_output = amount

    def record_model_usage(self, info: dict[str, Any]) -> None:
        with self._lock:
            if self._active_reservation is None:
                raise ValidationError("Usage reported without a model reservation")
            reservation = self._active_reservation
            self._active_reservation = None
            self._reserved_output = 0
            input_tokens, output_tokens = info.get("input_tokens"), info.get("output_tokens")
            known = bool(info.get("usage_known")) and all(
                isinstance(x, int) and not isinstance(x, bool) and x >= 0
                for x in (input_tokens, output_tokens)
            )
            if known:
                self.input_tokens += input_tokens
                self.output_tokens += output_tokens
            else:
                self.unknown_usage_calls += 1
                if (
                    isinstance(input_tokens, int)
                    and not isinstance(input_tokens, bool)
                    and input_tokens >= 0
                ):
                    self.input_tokens += input_tokens
                observed_output = (
                    output_tokens
                    if isinstance(output_tokens, int)
                    and not isinstance(output_tokens, bool)
                    and output_tokens >= 0
                    else 0
                )
                self.output_tokens += max(reservation, observed_output)
            if (
                self.limits.max_input_tokens is not None
                and self.input_tokens > self.limits.max_input_tokens
            ) or (
                self.limits.max_output_tokens is not None
                and self.output_tokens > self.limits.max_output_tokens
            ):
                raise BudgetExceeded(
                    "Provider-reported usage exceeded remaining budget; stopping before another call"
                )

    def can_compile(self, max_output_tokens: int = 8192) -> bool:
        return (
            (
                self.limits.max_wall_seconds is None
                or self.elapsed_seconds < self.limits.max_wall_seconds
            )
            and (
                self.limits.max_model_calls is None
                or self.model_calls < self.limits.max_model_calls
            )
            and (
                self.limits.max_input_tokens is None
                or self.input_tokens < self.limits.max_input_tokens
            )
            and (
                self.limits.max_output_tokens is None
                or self.output_tokens + max_output_tokens <= self.limits.max_output_tokens
            )
            and self._active_reservation is None
        )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "limits": asdict(self.limits),
                "tool_calls": self.tool_calls,
                "model_calls": self.model_calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "unknown_usage_calls": self.unknown_usage_calls,
                "reserved_output": self._reserved_output,
                "elapsed_seconds": self.elapsed_seconds,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        obj = cls(BudgetLimits(**data["limits"]))
        for name in (
            "tool_calls",
            "model_calls",
            "input_tokens",
            "output_tokens",
            "unknown_usage_calls",
        ):
            value = data.get(name, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError("Invalid persisted budget")
            setattr(obj, name, value)
        obj._elapsed = float(data.get("elapsed_seconds", 0))
        if obj._elapsed < 0 or not obj._elapsed < float("inf"):
            raise ValidationError("Invalid persisted elapsed time")
        # A process may have died during a provider call. Never refund it.
        reservation = data.get("reserved_output", 0)
        if not isinstance(reservation, int) or isinstance(reservation, bool) or reservation < 0:
            raise ValidationError("Invalid persisted reservation")
        if reservation:
            obj.output_tokens += reservation
            obj.unknown_usage_calls += 1
        return obj
