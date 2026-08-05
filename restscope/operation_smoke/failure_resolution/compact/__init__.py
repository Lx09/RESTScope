"""Expose Resolution's internal local-compaction Agent and instruction."""

from .agent import FailureResolutionCompactAgent, FailureResolutionCompactError
from .prompts import COMPACT_INSTRUCTION

__all__ = [
    "COMPACT_INSTRUCTION",
    "FailureResolutionCompactAgent",
    "FailureResolutionCompactError",
]
