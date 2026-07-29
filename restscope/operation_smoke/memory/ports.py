"""Declare the transaction seam used by the Operation Smoke Memory Module.

The runtime Module depends on this narrow protocol rather than SQLAlchemy.
Production supplies a database Adapter while focused tests may supply another
Adapter without changing Planner or Failure Solve code.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from .schemas import (
    FailureCatalogEntry,
    FailureHistory,
    InvestigationWrite,
    ParameterHistory,
    PlanMemoryWrite,
    RecordedPlan,
)


class SmokeMemoryRepository(Protocol):
    """Store and query structured Operation Smoke knowledge."""

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Persist validated classifications and their Observation links."""
        ...

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one Solve result and return its durable Investigation ID."""
        ...

    def list_operation_failures(
        self,
        operation_key: str,
    ) -> list[FailureCatalogEntry]:
        """Return a compact catalog for one operation's Planner prompt."""
        ...

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Return complete structured history for operation-scoped Failure IDs."""
        ...

    def lookup_parameter_history(
        self,
        operation_key: str,
        input_node_ids: list[str],
    ) -> list[ParameterHistory]:
        """Return Failures and repairs previously linked to selected inputs."""
        ...


class SmokeMemoryUnitOfWork(Protocol):
    """Own one database transaction around Memory repository calls."""

    smoke_memory: SmokeMemoryRepository

    def __enter__(self) -> "SmokeMemoryUnitOfWork":
        """Open one transaction-scoped repository view."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the transaction, rolling back when an exception escaped."""
        ...

    def commit(self) -> None:
        """Make every write in the current transaction visible together."""
        ...

    def rollback(self) -> None:
        """Discard every uncommitted write in the current transaction."""
        ...
