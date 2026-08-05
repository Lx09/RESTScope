"""Transaction wrapper for stable Failure and terminal Resolution knowledge."""

from __future__ import annotations

from .ports import SmokeMemoryUnitOfWorkFactory
from .schemas import (
    FailureBatchWrite,
    FailureHistory,
    ParameterHistory,
    RecordedFailures,
    SolveAttemptWrite,
)


class SmokeMemory:
    """Hide database sessions from Failure Resolution and Patch finalization."""

    def __init__(self, unit_of_work_factory: SmokeMemoryUnitOfWorkFactory) -> None:
        """Store the factory used for one short transaction per operation."""

        self.unit_of_work_factory = unit_of_work_factory

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Upsert stable Failure occurrences and return their identities."""

        with self.unit_of_work_factory() as uow:
            recorded = uow.smoke_memory.record_failures(write)
            uow.commit()
            return recorded

    def record_solve_attempt(self, write: SolveAttemptWrite) -> str:
        """Append one terminal no-Patch or conflict conclusion."""

        with self.unit_of_work_factory() as uow:
            attempt_id = uow.smoke_memory.record_solve_attempt(write)
            uow.commit()
            return attempt_id

    def failure_history(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> FailureHistory:
        """Read one stable Failure and all terminal attempts in order."""

        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.failure_history(
                operation_key=operation_key,
                failure_id=failure_id,
            )

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Read Failures previously attributed to one exact input node."""

        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.parameter_history(
                operation_key=operation_key,
                input_node_id=input_node_id,
            )
