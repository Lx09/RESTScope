"""Persistence ports for resource-identifier evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import Protocol, TypeAlias

from .resource_schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MonitoredOperation,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorWarning,
    ResourceNameSummary,
)


class ResourceCatalogRepository(Protocol):
    """
    Define the collaborator contract for resource catalog repository.

    Concrete implementations may vary while callers in API response monitoring and its
    narrowly approved evidence catalog depend only on these declared operations.
    """
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
    """
    Define the collaborator contract for resource catalog unit of work.

    Concrete implementations may vary while callers in API response monitoring and its
    narrowly approved evidence catalog depend only on these declared operations.
    """
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
