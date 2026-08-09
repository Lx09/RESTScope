"""Expose the App and its configuration without performing runtime work.

Importing this package does not read environment configuration, create files,
configure logging, open a database, or construct an Agent. Domain Interfaces
remain available from their explicit owning packages.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "RESTScopeApp",
    "RESTScopeConfig",
]


_PUBLIC_MODULE_BY_NAME = {
    "RESTScopeApp": ".app",
    "RESTScopeConfig": ".config",
}


def __getattr__(name: str) -> Any:
    """Resolve one approved facade name from its owning module.

    Raises:
        AttributeError: The requested symbol is internal or does not exist.
    """
    module_name = _PUBLIC_MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the supported facade Interface to readers and IDEs."""
    return sorted(set(globals()) | set(__all__))
