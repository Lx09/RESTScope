"""Persistence ports for generator configuration."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, TypeAlias

from .models import InputGeneratorConfig, OperationGeneratorConfig


class GeneratorConfigConcurrentWrite(RuntimeError):
    """The persisted revision changed after the application-level read."""


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
    ) -> OperationGeneratorConfig: ...

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
