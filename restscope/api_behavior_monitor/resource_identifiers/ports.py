"""Persistence ports for resource-identifier evidence."""

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
    ResourceIdentifierPage,
    ResourceMonitorWarning,
    ResourceNameSummary,
)


class ResourceCatalogRepository(Protocol):
    """Define the resource, alias, selector, identifier, usage, and monitor-error persistence operations used by the domain Catalog."""
    def record_groups(
        self,
        *,
        operation: MonitoredOperation,
        groups: list[DetectedResourceGroup],
        observed_at: datetime,
    ) -> None: ...

    def list_rules(self, operation: MonitoredOperation) -> list[LearnedResourceRule]: ...

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]: ...

    def list_resource_names(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[str], int]: ...

    def list_identifiers(
        self,
        *,
        resource: str,
        offset: int,
        limit: int,
    ) -> ResourceIdentifierPage: ...

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
    """Expose one atomic Resource Catalog transaction and its repository."""
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
