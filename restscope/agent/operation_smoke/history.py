"""Keep complete Operation Smoke evidence for the lifetime of one App.

The ledger is intentionally an in-memory object owned by ``OperationSmokeAgent``.
It never writes raw responses, model reasoning, plans, or Constraints to the
database. State is isolated by operation so Supervisor retries can learn from
earlier attempts without sharing evidence across unrelated endpoints.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from restscope.agent.parameter_patch import CompiledConstraintPatch


@dataclass
class OperationSmokeHistoryState:
    """Store one operation's chronological records and accepted Constraints."""

    records: list[dict[str, Any]] = field(default_factory=list)
    accepted_constraints: dict[str, CompiledConstraintPatch] = field(
        default_factory=dict
    )


class OperationSmokeHistory:
    """Own all App-lifetime, non-persistent Smoke state."""

    def __init__(self) -> None:
        """Start with no operation evidence or learned Constraints."""
        self._states: dict[str, OperationSmokeHistoryState] = {}

    def state_for(self, operation_key: str) -> OperationSmokeHistoryState:
        """Return the mutable state isolated to ``operation_key``."""
        return self._states.setdefault(
            operation_key,
            OperationSmokeHistoryState(),
        )

    def snapshot(self, operation_key: str) -> list[dict[str, Any]]:
        """Return a deep copy suitable for an LLM prompt."""
        return deepcopy(self.state_for(operation_key).records)

    def record(self, operation_key: str, record: dict[str, Any]) -> None:
        """Append complete chronological evidence without persistence."""
        self.state_for(operation_key).records.append(deepcopy(record))

    def clear(self) -> None:
        """Release every operation's raw evidence and runtime Constraints."""
        self._states.clear()
