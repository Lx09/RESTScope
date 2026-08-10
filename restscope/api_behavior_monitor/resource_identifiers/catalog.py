"""Transaction boundary for persisted resource-identifier facts."""

from __future__ import annotations

from datetime import UTC, datetime

from .ports import ResourceCatalogUnitOfWorkFactory
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


class ResourceCatalog:
    """Persist and query the narrow resource evidence approved for one App."""

    def __init__(self, unit_of_work_factory: ResourceCatalogUnitOfWorkFactory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def record_groups(
        self,
        *,
        operation: MonitoredOperation,
        groups: list[DetectedResourceGroup],
        observed_at: datetime | None = None,
    ) -> None:
        """Atomically persist resources, definitions, complete records, and usage."""
        timestamp = observed_at or datetime.now(UTC)
        with self.unit_of_work_factory() as uow:
            uow.resources.record_groups(
                operation=operation,
                groups=groups,
                observed_at=timestamp,
            )
            uow.commit()

    def list_rules(
        self,
        operation: MonitoredOperation,
    ) -> list[LearnedResourceRule]:
        """Return learned resource selector rules for one exact operation."""
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_rules(operation)

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]:
        """Return a bounded page of canonical resources and aliases."""
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_resources(
                limit=limit,
                aliases_per_resource=aliases_per_resource,
            )

    def list_resource_names(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[str], int]:
        """Return one alphabetical page of canonical resource names.

        Args:
            offset: Number of matching resources to skip.
            limit: Maximum names to return in this page.

        Returns:
            The selected canonical names and the total resource count. Aliases,
            identifiers, operation usage, and Monitor errors stay hidden behind
            the Catalog because discovery callers do not need them.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_resource_names(
                offset=offset,
                limit=limit,
            )

    def list_identifiers(
        self,
        *,
        resource: str,
        offset: int,
        limit: int,
    ) -> ResourceIdentifierPage:
        """Return a typed identifier page for one canonical name or alias.

        Args:
            resource: Canonical resource name or learned alias.
            offset: Number of identifiers in recency order to skip.
            limit: Maximum identifiers in this page.

        Returns:
            A found page or an explicit not-found page. Operation usage and
            Monitor errors are deliberately outside this narrow read.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_identifiers(
                resource=resource,
                offset=offset,
                limit=limit,
            )

    def record_error(
        self,
        *,
        operation: MonitoredOperation,
        group_path: str,
        warning: ResourceMonitorWarning,
        observed_at: datetime | None = None,
    ) -> None:
        """Persist one bounded resource-monitor error for later inspection."""
        with self.unit_of_work_factory() as uow:
            uow.resources.record_error(
                operation=operation,
                group_path=group_path,
                warning=warning,
                observed_at=observed_at or datetime.now(UTC),
            )
            uow.commit()

    def record_operation_error(
        self,
        *,
        operation: MonitoredOperation,
        warning: ResourceMonitorWarning,
        observed_at: datetime | None = None,
    ) -> None:
        """Persist one operation-scoped resource-monitor error without raw response data."""
        with self.unit_of_work_factory() as uow:
            uow.resources.record_operation_error(
                operation=operation,
                warning=warning,
                observed_at=observed_at or datetime.now(UTC),
            )
            uow.commit()

    def clear_operation_errors(self, operation_key: str) -> None:
        """Clear retained resource-monitor errors after one operation is observed successfully."""
        with self.unit_of_work_factory() as uow:
            uow.resources.clear_operation_errors(operation_key)
            uow.commit()

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult:
        """
        Look up bounded evidence used by API response monitoring and its narrowly
        approved evidence catalog.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.lookup(request)
