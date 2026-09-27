"""Flora: explicit effects, open programs, and scoped evidence.

The standard-library runtime is independent of any agent framework.
"""

__version__ = "0.1.0"

from flora.agent.api import Agent, AgentRunError, SessionBusyError, SessionStateError, invoke
from flora.engine.budget import BudgetLimits
from flora.engine.runtime import RunResult, RuntimeConfig
from flora.integrations.binding import make_registry, tool
from flora.integrations.tools import ToolRegistry, ToolSpec

__all__ = [
    "Agent",
    "AgentRunError",
    "BudgetLimits",
    "RunResult",
    "RuntimeConfig",
    "SessionBusyError",
    "SessionStateError",
    "ToolRegistry",
    "ToolSpec",
    "invoke",
    "make_registry",
    "tool",
]

from ._compat import install as _install_aliases

_install_aliases(globals())
del _install_aliases
