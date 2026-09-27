"""Aliases sharing the same module objects as the organized implementations."""

import sys
from importlib import import_module

_ALIASES = {
    "api": "agent.api",
    "runtime": "engine.runtime",
    "scheduler": "engine.scheduler",
    "effects": "engine.effects",
    "budget": "engine.budget",
    "replay": "engine.replay",
    "compiler": "language.compiler",
    "ir": "language.ir",
    "vm": "language.vm",
    "contracts": "checks.contracts",
    "diagnostics": "checks.diagnostics",
    "reuse": "checks.reuse",
    "providers": "integrations.providers",
    "tools": "integrations.tools",
    "binding": "integrations.binding",
    "adapters": "integrations.adapters",
    "workspace": "integrations.workspace",
    "session": "state.session",
    "trace": "state.trace",
    "opaque": "state.opaque",
    "errors": "support.errors",
    "resources": "support.resources",
    "values": "support.values",
    "cli": "interface.cli",
    "console": "interface.console",
    "settings": "interface.settings",
}


def install(namespace):
    for name, target in _ALIASES.items():
        module = import_module("flora." + target)
        sys.modules["flora." + name] = module
        namespace[name] = module
