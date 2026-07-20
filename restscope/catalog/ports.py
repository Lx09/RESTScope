"""Persistence ports consumed by the schema catalog service."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, TypeAlias

from .models import SchemaRecord


class SchemaRepository(Protocol):
    def add(self, *, id: str, file_path: str | None, raw_content: str | None) -> SchemaRecord: ...

    def get(self, schema_id: str) -> SchemaRecord | None: ...

    def list(self) -> list[SchemaRecord]: ...

    def replace_source(
        self,
        schema_id: str,
        *,
        file_path: str | None,
        raw_content: str | None,
    ) -> SchemaRecord | None: ...


class SchemaUnitOfWork(Protocol):
    schemas: SchemaRepository

    def __enter__(self) -> "SchemaUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


SchemaUnitOfWorkFactory: TypeAlias = Callable[[], SchemaUnitOfWork]
