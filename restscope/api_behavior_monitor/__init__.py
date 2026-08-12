"""Expose the API Behavior Monitor workflow without loading its database Adapter.

Several database repositories use Monitor-owned domain types.  Eagerly importing
the workflow factory from this facade would make those repositories import the
database package again while it is still initializing.  Lazy resolution keeps
the facade small and preserves the intended dependency direction.
"""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "APIBehaviorCatalog",
    "APIBehaviorMonitorCoordinator",
    "APIBehaviorMonitorError",
    "APIBehaviorMonitorResult",
    "APIBehaviorResponseProcessor",
    "APIBehaviorWarning",
    "build_api_behavior_monitor_coordinator",
]

_PUBLIC_MODULE_BY_NAME = {
    "APIBehaviorCatalog": ".catalog",
    "APIBehaviorMonitorCoordinator": ".coordinator",
    "APIBehaviorMonitorError": ".coordinator",
    "APIBehaviorMonitorResult": ".results",
    "APIBehaviorResponseProcessor": ".response_processor",
    "APIBehaviorWarning": ".results",
    "build_api_behavior_monitor_coordinator": ".factory",
}


def __getattr__(name: str) -> object:
    """Resolve an approved workflow export from the module that owns it."""
    module_name = _PUBLIC_MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show only the supported workflow Interface to readers and IDEs."""
    return sorted(set(globals()) | set(__all__))
