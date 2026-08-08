"""Deterministic Agent and testing Harness construction Interface.

The facade resolves runtime objects lazily so Tool implementations may depend
on Harness-owned session state without constructing the App runtime while the
global Tool Catalog itself is importing.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "HarnessRuntime",
    "AgentRuntimeNotConfiguredError",
    "AgentRuntimeDefinition",
    "ContextSourceBinding",
    "ToolBindingFactory",
    "OperationAttempt",
    "RESTScopeRunReport",
    "RESTScopeRunRequest",
    "RunHarness",
    "build_harness",
    "build_harness_with_mcp_host",
]


def __getattr__(name: str) -> Any:
    """Load one approved runtime export on first use."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    run_exports = {
        "OperationAttempt",
        "RESTScopeRunReport",
        "RESTScopeRunRequest",
        "RunHarness",
    }
    module_name = (
        ".run"
        if name in run_exports
        else ".agent_runtime"
        if name in {"AgentRuntimeDefinition", "ContextSourceBinding", "ToolBindingFactory"}
        else ".runtime"
    )
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the small public Harness Interface."""
    return sorted(set(globals()) | set(__all__))
