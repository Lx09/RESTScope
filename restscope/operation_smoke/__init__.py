"""Expose the small public Interface of the Operation Smoke workflow.

The workflow package contains Coordinator, Agent, Memory, and persistence-facing
code.  Importing a nested module such as ``operation_smoke.memory`` must not
construct that entire dependency graph: the database Adapter itself imports the
Memory DTOs while the Coordinator also depends on database-backed catalogs.
This facade therefore resolves its approved public names lazily.

Callers still use ordinary imports such as
``from restscope.operation_smoke import OperationSmokeRequest``.  The lazy
lookup is an import-cycle boundary, not a compatibility alias: only names in
``__all__`` are available and every name resolves to its one current owner.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "BehaviorMonitorReferenceValues",
    "OperationSmokeCoordinator",
    "OperationSmokeRequest",
    "OperationSmokeResult",
    "PatchAttemptSummary",
    "SmokeRoundSummary",
    "SmokeBatchRunner",
    "TodoRunSummary",
    "build_operation_smoke_coordinator",
]


# The mapping keeps the facade declarative and makes ownership easy to locate.
# It intentionally contains no Agent classes: Agents are internal to this
# workflow and are imported through their named subpackages when necessary.
_PUBLIC_MODULE_BY_NAME = {
    "BehaviorMonitorReferenceValues": ".references",
    "OperationSmokeCoordinator": ".coordinator",
    "OperationSmokeRequest": ".schemas",
    "OperationSmokeResult": ".schemas",
    "PatchAttemptSummary": ".schemas",
    "SmokeRoundSummary": ".schemas",
    "SmokeBatchRunner": ".coordinator",
    "TodoRunSummary": ".schemas",
    "build_operation_smoke_coordinator": ".factory",
}


def __getattr__(name: str) -> Any:
    """Load one approved facade export when a caller first requests it.

    Args:
        name: Attribute requested from the workflow package.

    Returns:
        The class, protocol, or builder owned by the mapped submodule.

    Raises:
        AttributeError: The requested name is not part of the public Interface.
    """
    module_name = _PUBLIC_MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    # Cache successful resolution so later imports have normal module-attribute
    # cost and identity semantics.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the approved Interface to readers and interactive tools."""
    return sorted(set(globals()) | set(__all__))
