"""Persistence ports for the Resource Monitor package."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, TypeAlias

from .schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MonitoredOperation,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorWarning,
    ResourceNameSummary,
)


class ResourceCatalogRepository(Protocol):
    def record_groups(
        self,
        *,
        operation: MonitoredOperation,
        groups: list[DetectedResourceGroup],
        observed_at: datetime,
    ) -> None: ...

    def list_rules(self, operation_key: str) -> list[LearnedResourceRule]: ...

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]: ...

    def record_error(
        self,
        *,
        operation: MonitoredOperation,
        group_path: str,
        warning: ResourceMonitorWarning,
        observed_at: datetime,
    ) -> None: ...

    def record_operation_error(
        self,
        *,
        operation: MonitoredOperation,
        warning: ResourceMonitorWarning,
        observed_at: datetime,
    ) -> None: ...

    def clear_operation_errors(self, operation_key: str) -> None: ...

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult: ...


class ResourceCatalogUnitOfWork(Protocol):
    resources: ResourceCatalogRepository

    def __enter__(self) -> "ResourceCatalogUnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


ResourceCatalogUnitOfWorkFactory: TypeAlias = Callable[[], ResourceCatalogUnitOfWork]
