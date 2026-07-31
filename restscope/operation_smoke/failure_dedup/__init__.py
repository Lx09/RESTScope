"""Public Interface for current-Batch Failure deduplication."""

from .agent import FailureDedupAgent
from .deduplicator import FailureDeduplicator
from .schemas import (
    FailureDedupDecision,
    FailureDedupRequest,
    FailureDedupResult,
    FailureGroupDecision,
    FailureTodo,
)

__all__ = [
    "FailureDedupAgent",
    "FailureDedupDecision",
    "FailureDedupRequest",
    "FailureDedupResult",
    "FailureDeduplicator",
    "FailureGroupDecision",
    "FailureTodo",
]
