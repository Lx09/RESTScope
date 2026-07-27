"""Transaction boundary for persisted resource-identifier facts."""

from __future__ import annotations

from datetime import datetime

from restscope.db.time import utc_now

from .resource_ports import ResourceCatalogUnitOfWorkFactory
from .resource_schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MonitoredOperation,
    ResourceLookupRequest,
    ResourceLookupResult,
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
        """
        Record groups for API response monitoring and its narrowly approved evidence
        catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        timestamp = observed_at or utc_now()
        with self.unit_of_work_factory() as uow:
            uow.resources.record_groups(
                operation=operation,
                groups=groups,
                observed_at=timestamp,
            )
            uow.commit()

    def list_rules(self, operation_key: str) -> list[LearnedResourceRule]:
        """
        Return rules for API response monitoring and its narrowly approved evidence
        catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_rules(operation_key)

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]:
        """
        Return resources for API response monitoring and its narrowly approved evidence
        catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.list_resources(
                limit=limit,
                aliases_per_resource=aliases_per_resource,
            )

    def record_error(
        self,
        *,
        operation: MonitoredOperation,
        group_path: str,
        warning: ResourceMonitorWarning,
        observed_at: datetime | None = None,
    ) -> None:
        """
        Record error for API response monitoring and its narrowly approved evidence
        catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            uow.resources.record_error(
                operation=operation,
                group_path=group_path,
                warning=warning,
                observed_at=observed_at or utc_now(),
            )
            uow.commit()

    def record_operation_error(
        self,
        *,
        operation: MonitoredOperation,
        warning: ResourceMonitorWarning,
        observed_at: datetime | None = None,
    ) -> None:
        """
        Record operation error for API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            uow.resources.record_operation_error(
                operation=operation,
                warning=warning,
                observed_at=observed_at or utc_now(),
            )
            uow.commit()

    def clear_operation_errors(self, operation_key: str) -> None:
        """
        Handle clear operation errors as part of API response monitoring and its
        narrowly approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            uow.resources.clear_operation_errors(operation_key)
            uow.commit()

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult:
        """
        Look up bounded evidence used by API response monitoring and its narrowly
        approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        with self.unit_of_work_factory() as uow:
            return uow.resources.lookup(request)
