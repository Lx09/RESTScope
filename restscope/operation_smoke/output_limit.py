"""Share one hard model-output limit across an Operation Smoke run.

Resolution, local Resolution Compact, Parameter Patch, and Patch Review all
receive the same instance. The counter is a last-resort safety guard, not a
per-Agent planning budget, and therefore exposes no reset or role-specific
allowance.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


class ModelOutputLimitExceeded(RuntimeError):
    """Stop Operation Smoke before it can request more than the hard maximum."""


@dataclass(frozen=True)
class ModelOutputUsage:
    """Report total and per-role usage after one reserved provider call."""

    used: int
    remaining: int
    by_role: dict[str, int]


class ModelOutputLimit:
    """Count every model call made by one Operation Smoke execution."""

    def __init__(self, *, max_outputs: int = 1_000) -> None:
        """Create the shared counter with the approved hard maximum."""
        if max_outputs < 1:
            raise ValueError("max_outputs must be positive")
        self.max_outputs = max_outputs
        self._used = 0
        self._by_role: Counter[str] = Counter()

    @property
    def used(self) -> int:
        """Return how many provider calls have reserved an output slot."""
        return self._used

    @property
    def remaining(self) -> int:
        """Return the number of calls still allowed before the hard stop."""
        return self.max_outputs - self._used

    def consume(self, role: str) -> ModelOutputUsage:
        """Reserve one output before calling a model, or stop without mutation."""
        if self._used >= self.max_outputs:
            raise ModelOutputLimitExceeded(
                f"Operation Smoke reached its hard limit of {self.max_outputs} "
                "model outputs."
            )
        self._used += 1
        self._by_role[role] += 1
        return ModelOutputUsage(
            used=self._used,
            remaining=self.remaining,
            by_role=dict(self._by_role),
        )
