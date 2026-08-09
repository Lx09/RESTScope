"""Persistence ports for current per-input Generator configuration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Protocol, TypeAlias

from .models import InputGeneratorConfig, ResourceIdentifierGenerator, ResponseValueGenerator
from .constraints import OperationConstraintRecord


class GeneratorConfigConcurrentWrite(RuntimeError):
    """The stored input content changed after a Patch candidate was prepared."""


class ReferenceValueProvider(Protocol):
    """Resolve persisted evidence used by reference-backed generators."""

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]: ...


class GeneratorConfigRepository(Protocol):
    """Persist only current Generator rows keyed by stable input-node identity."""

    def initialize(self, records: list[tuple[str, list[InputGeneratorConfig]]]) -> None: ...

    def get_inputs(self, operation_key: str) -> list[InputGeneratorConfig]: ...

    def replace_inputs(
        self,
        *,
        operation_key: str,
        expected: list[InputGeneratorConfig],
        updated: list[InputGeneratorConfig],
    ) -> None: ...

    def get_constraints(self, operation_key: str) -> list[OperationConstraintRecord]: ...

    def replace_constraints(
        self,
        *,
        operation_key: str,
        expected: list[OperationConstraintRecord],
        updated: list[OperationConstraintRecord],
    ) -> None: ...

    def record_change_event(
        self,
        *,
        solve_attempt_id: str,
        operation_key: str,
        reason: str,
        generator_changes: list[dict],
        constraint_changes: list[dict],
    ) -> str: ...


class GeneratorConfigUnitOfWork(Protocol):
    """Expose one transaction around current Generator rows."""

    generator_configs: GeneratorConfigRepository

    def __enter__(self) -> "GeneratorConfigUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


GeneratorConfigUnitOfWorkFactory: TypeAlias = Callable[[], GeneratorConfigUnitOfWork]
