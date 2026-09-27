# SPDX-License-Identifier: Apache-2.0
"""Public error classes. Budget and effect uncertainty must not be retried."""


class FloraError(Exception):
    """Base class for explicit Flora failures."""


# Import compatibility inside the new package; no legacy package is installed.
OpenHarnessError = FloraError


class ValidationError(FloraError, ValueError):
    """Invalid JSON, IR, tool arguments, or serialized runtime state."""


class BudgetExceeded(FloraError):
    """An execution or spending budget has been exhausted."""


class StaleAnchor(FloraError):
    """A proposal no longer names the current committed frontier."""


class InterruptedEffect(FloraError):
    """An effect may have happened, but its outcome is unknown."""


class CompilerError(FloraError):
    """The compiler could not produce a valid bounded proposal."""


class TraceIntegrityError(FloraError):
    """A persisted or replayed trace failed an integrity check."""


class MachineStateError(ValidationError):
    """Invalid machine state or an invalid resume operation."""
