"""Persistence ports for stable Failure and terminal Attempt knowledge."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, TypeAlias

from .schemas import (
    FailureBatchWrite,
    FailureHistory,
    ParameterHistory,
    RecordedFailures,
    SolveAttemptWrite,
)


class SmokeMemoryRepository(Protocol):
    """Describe exact reads and writes used by Resolution finalization."""

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures: ...

    def record_solve_attempt(self, write: SolveAttemptWrite) -> str: ...

    def failure_history(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> FailureHistory: ...

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory: ...


class SmokeMemoryUnitOfWork(Protocol):
    """Expose Smoke and Generator repositories on one transaction when needed."""

    smoke_memory: SmokeMemoryRepository

    def __enter__(self) -> "SmokeMemoryUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


SmokeMemoryUnitOfWorkFactory: TypeAlias = Callable[[], SmokeMemoryUnitOfWork]
