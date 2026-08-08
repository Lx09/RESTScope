"""Expose the small, lazily loaded Failure Resolution workflow Interface.

Tool Modules import Worklist and candidate DTOs without constructing the LLM
Agent and its complete dependency graph. Public callers still import the same
Harness-owned session state and finalization names from this facade.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


__all__ = [
    "FailureResolutionFinish",
    "FailureResolutionFinalizer",
    "FailureResolutionAgent",
    "FailureResolutionOutcome",
    "FailureResolutionRequest",
    "FailureSource",
    "FailureWorklist",
    "FailureWorklistStore",
    "PatchCandidateRegistry",
    "PatchCandidateSummary",
    "ResolutionCommit",
    "ResolutionFinalizer",
    "ResolutionItemCommit",
    "ResolutionUnitOfWork",
    "WorklistDecision",
    "WorklistItem",
    "build_failure_sources",
    "derive_failure_summary",
]


_PUBLIC_MODULE_BY_NAME = {
    "FailureResolutionAgent": ".agent",
    "ResolutionFinalizer": ".agent",
    "build_failure_sources": ".agent",
    "FailureResolutionFinalizer": ".finalizer",
    "ResolutionUnitOfWork": ".finalizer",
    "derive_failure_summary": ".finalizer",
    "PatchCandidateRegistry": ".candidates",
    "PatchCandidateSummary": ".candidates",
    "FailureWorklistStore": ".worklist",
    "FailureResolutionFinish": ".schemas",
    "FailureResolutionOutcome": ".schemas",
    "FailureResolutionRequest": ".schemas",
    "FailureSource": ".schemas",
    "FailureWorklist": ".schemas",
    "WorklistDecision": ".schemas",
    "WorklistItem": ".schemas",
    "ResolutionCommit": ".schemas",
    "ResolutionItemCommit": ".schemas",
}


def __getattr__(name: str) -> Any:
    """Resolve one approved workflow name without eager Agent construction."""
    module_name = _PUBLIC_MODULE_BY_NAME.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Show the approved workflow Interface to readers and interactive tools."""
    return sorted(set(globals()) | set(__all__))
