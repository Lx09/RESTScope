"""Expose the deep Operation Smoke Memory Interface to workflow callers.

The Module owns transaction lifetime. Failure Dedup writes validated
current-round Failures, while Solve reads Failure and Parameter history and
records terminal Investigations. Callers never coordinate database sessions.
"""

from __future__ import annotations

from collections.abc import Callable

from .ports import SmokeMemoryUnitOfWork
from .schemas import (
    FailureBatchWrite,
    FailureHistory,
    InvestigationWrite,
    ParameterHistory,
    RecordedFailures,
)


class SmokeMemoryReferenceError(ValueError):
    """A Failure or Parameter reference does not belong to the operation."""


class SmokeMemory:
    """Persist and retrieve structured Failure knowledge for one App database."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], SmokeMemoryUnitOfWork],
    ) -> None:
        """Bind the transaction Adapter without opening a session."""
        self.unit_of_work_factory = unit_of_work_factory

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Atomically record one complete validated Dedup result."""
        with self.unit_of_work_factory() as uow:
            result = uow.smoke_memory.record_failures(write)
            uow.commit()
            return result

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one terminal Solve conclusion and return its identity."""
        with self.unit_of_work_factory() as uow:
            investigation_id = uow.smoke_memory.record_investigation(write)
            uow.commit()
            return investigation_id

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Return ordered histories or reject cross-operation references."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.lookup_failure_history(
                operation_key,
                failure_ids,
            )

    def lookup_parameter_history(
        self,
        operation_key: str,
        input_node_ids: list[str],
    ) -> list[ParameterHistory]:
        """Return histories for exact operation inputs in caller order."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.lookup_parameter_history(
                operation_key,
                input_node_ids,
            )
