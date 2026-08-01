"""Persistence ports consumed by the OpenAPI audit catalog."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, TypeAlias

from .models import OpenAPIChangeEventRecord, OpenAPIChangeEventWrite


class OpenAPIRepository(Protocol):
    """Define current-document and append-only event persistence operations."""

    def initialize(self, document: dict[str, Any]) -> None: ...

    def get_current(self) -> dict[str, Any] | None: ...

    def record_change(
        self,
        *,
        document: dict[str, Any],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord: ...

    def list_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]: ...


class OpenAPIUnitOfWork(Protocol):
    """Expose one transaction around the OpenAPI audit repository."""

    openapi: OpenAPIRepository

    def __enter__(self) -> "OpenAPIUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


OpenAPIUnitOfWorkFactory: TypeAlias = Callable[[], OpenAPIUnitOfWork]
