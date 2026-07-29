"""Expose the deep Operation Smoke Memory Interface to workflow callers.

``SmokeMemory`` owns transaction lifetime and cross-call invariants while its
Adapter owns storage mechanics.  Planner and Failure Solve therefore use five
domain operations instead of coordinating sessions, ORM rows, joins, or
commits themselves.
"""

from __future__ import annotations

from collections.abc import Callable

from .ports import SmokeMemoryUnitOfWork
from .schemas import (
    FailureCatalogEntry,
    FailureHistory,
    InvestigationWrite,
    ParameterHistory,
    PlanMemoryWrite,
    RecordedPlan,
)


class SmokeMemoryReferenceError(ValueError):
    """A Failure or Parameter reference does not belong to the requested operation."""


class SmokeMemory:
    """Persist and retrieve structured Failure knowledge for one App database."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], SmokeMemoryUnitOfWork],
    ) -> None:
        """Bind the transaction Adapter without opening a database session."""
        self.unit_of_work_factory = unit_of_work_factory

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Atomically record one complete validated Planner classification."""
        with self.unit_of_work_factory() as uow:
            result = uow.smoke_memory.record_plan(write)
            uow.commit()
            return result

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one terminal Solve conclusion and return its stable identity."""
        with self.unit_of_work_factory() as uow:
            investigation_id = uow.smoke_memory.record_investigation(write)
            uow.commit()
            return investigation_id

    def list_operation_failures(
        self,
        operation_key: str,
    ) -> list[FailureCatalogEntry]:
        """Return the compact Failure directory always included for Planner."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.list_operation_failures(operation_key)

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Return ordered histories or fail if any reference crosses operations."""
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
