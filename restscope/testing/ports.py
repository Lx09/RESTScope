"""Persistence ports for generator configuration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Protocol, TypeAlias

from .models import (
    GeneratorConfigRevision,
    InputGeneratorConfig,
    OperationGeneratorConfig,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)


class GeneratorConfigConcurrentWrite(RuntimeError):
    """The persisted revision changed after the application-level read."""


class ReferenceValueProvider(Protocol):
    """Resolve persisted evidence used by reference-backed generators."""

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]: ...


class GeneratorConfigRepository(Protocol):
    def is_initialized(self) -> bool: ...

    def initialize(self, records: list[OperationGeneratorConfig]) -> None: ...

    def get(self, operation_key: str) -> OperationGeneratorConfig | None: ...

    def replace(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        revision: int,
        snapshot: dict,
        enabled: bool,
        disabled_reasons: list[dict],
        active_media_type: str | None,
        configs: list[InputGeneratorConfig],
        lifecycle: str = "accepted",
        hypothesis: dict | None = None,
        evaluation: dict | None = None,
        rollback_of_revision: int | None = None,
        restored_from_revision: int | None = None,
    ) -> OperationGeneratorConfig: ...

    def get_revision(
        self,
        operation_key: str,
        revision: int,
    ) -> GeneratorConfigRevision | None: ...

    def list_revisions(
        self,
        operation_key: str,
    ) -> list[GeneratorConfigRevision]: ...

    def update_revision(
        self,
        *,
        operation_key: str,
        revision: int,
        expected_lifecycle: str,
        lifecycle: str,
        evaluation: dict | None,
    ) -> GeneratorConfigRevision: ...


class GeneratorConfigUnitOfWork(Protocol):
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
