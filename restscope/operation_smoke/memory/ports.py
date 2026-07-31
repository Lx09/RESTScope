"""Declare the transaction seam used by the Operation Smoke Memory Module.

The runtime Module depends on this narrow protocol rather than SQLAlchemy.
Production supplies a database Adapter while focused tests may supply another
Adapter without changing Failure Dedup or Failure Solve code.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol

from .schemas import (
    FailureBatchWrite,
    FailureHistory,
    InvestigationWrite,
    ParameterHistory,
    RecordedFailures,
)


class SmokeMemoryRepository(Protocol):
    """Store and query structured Operation Smoke knowledge."""

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Persist validated current-round Failures and Observation links."""
        ...

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one Solve result and return its durable Investigation ID."""
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
