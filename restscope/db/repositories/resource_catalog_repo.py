"""SQLAlchemy adapter for resource catalog persistence and lookup."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..orm import (
    OperationResourceRuleORM,
    ResourceAliasORM,
    ResourceIdentifierORM,
    ResourceMonitorErrorORM,
    ResourceOperationUsageORM,
    ResourceORM,
)
from ..time import as_utc

if TYPE_CHECKING:
    from restscope.api_behavior_monitor.resource_schemas import (
        DetectedResourceGroup,
        LearnedResourceRule,
        MonitoredOperation,
        ResourceLookupRequest,
        ResourceLookupResult,
        ResourceMonitorWarning,
        ResourceNameSummary,
        ResourceOperationSummary,
    )


class ResourceCatalogConflict(ValueError):
    """A learned alias or operation group contradicts existing catalog facts."""

    code = "resource_catalog_conflict"


class SqlAlchemyResourceCatalogRepository:
    """
    Define the collaborator contract for sql alchemy resource catalog repository.

    Concrete implementations may vary while callers in the repository and database
    persistence boundary depend only on these declared operations.
    """
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_groups(
        self,
        *,
        operation: MonitoredOperation,
        groups: list[DetectedResourceGroup],
        observed_at: datetime,
    ) -> None:
        """
        Record groups for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        for group in groups:
            if not group.has_resource:
                self._upsert_rule(
                    resource=None,
                    operation=operation,
                    group=group,
                )
                self.session.execute(
                    delete(ResourceMonitorErrorORM).where(
                        ResourceMonitorErrorORM.operation_key == operation.operation_key,
                        ResourceMonitorErrorORM.group_path == group.group_path,
                    )
                )
                continue
            resource = self._resolve_resource(group)
            assert group.resource_name is not None
            self._add_aliases(resource, [group.resource_name, *group.resource_aliases])
            rule = self._upsert_rule(
                resource=resource,
                operation=operation,
                group=group,
            )
            for value in group.identifier_values:
                identifier = self._upsert_identifier(
                    resource=resource,
                    value=value,
                    observed_at=observed_at,
                )
                self._upsert_usage(
                    identifier=identifier,
                    rule=rule,
                    observed_at=observed_at,
                )
            self.session.execute(
                delete(ResourceMonitorErrorORM).where(
                    ResourceMonitorErrorORM.operation_key == operation.operation_key,
                    ResourceMonitorErrorORM.group_path == group.group_path,
                )
            )
        self.session.flush()

    def list_rules(self, operation_key: str) -> list[LearnedResourceRule]:
        """
        Return rules for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        rows = self.session.scalars(
            select(OperationResourceRuleORM)
            .where(OperationResourceRuleORM.operation_key == operation_key)
            .order_by(OperationResourceRuleORM.group_path)
        ).all()
        return [self._to_rule(row) for row in rows]

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]:
        """
        Return resources for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        from restscope.api_behavior_monitor.resource_schemas import (
            ResourceNameSummary,
        )

        resource_query = select(ResourceORM).order_by(
                func.lower(ResourceORM.canonical_name),
                ResourceORM.canonical_name,
            )
        if limit is not None:
            resource_query = resource_query.limit(limit)
        rows = self.session.scalars(resource_query).all()
        aliases_by_resource: dict[str, list[str]] = {row.id: [] for row in rows}
        if rows:
            resource_ids = [row.id for row in rows]
            alias_order = (func.lower(ResourceAliasORM.alias), ResourceAliasORM.alias)
            if aliases_per_resource is None:
                alias_query = (
                    select(ResourceAliasORM.resource_id, ResourceAliasORM.alias)
                    .where(ResourceAliasORM.resource_id.in_(resource_ids))
                    .order_by(ResourceAliasORM.resource_id, *alias_order)
                )
            else:
                ranked_aliases = (
                    select(
                        ResourceAliasORM.resource_id.label("resource_id"),
                        ResourceAliasORM.alias.label("alias"),
                        func.row_number()
                        .over(
                            partition_by=ResourceAliasORM.resource_id,
                            order_by=alias_order,
                        )
                        .label("alias_rank"),
                    )
                    .where(ResourceAliasORM.resource_id.in_(resource_ids))
                    .subquery()
                )
                alias_query = (
                    select(
                        ranked_aliases.c.resource_id,
                        ranked_aliases.c.alias,
                    )
                    .where(ranked_aliases.c.alias_rank <= aliases_per_resource)
                    .order_by(
                        ranked_aliases.c.resource_id,
                        func.lower(ranked_aliases.c.alias),
                        ranked_aliases.c.alias,
                    )
                )
            for resource_id, alias in self.session.execute(alias_query):
                aliases_by_resource[resource_id].append(alias)
        output: list[ResourceNameSummary] = []
        for row in rows:
            output.append(
                ResourceNameSummary(
                    resource_id=row.id,
                    canonical_name=row.canonical_name,
                    aliases=aliases_by_resource[row.id],
                )
            )
        return output

    def record_error(
        self,
        *,
        operation: MonitoredOperation,
        group_path: str,
        warning: ResourceMonitorWarning,
        observed_at: datetime,
    ) -> None:
        """
        Record error for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        rule = self.session.scalar(
            select(OperationResourceRuleORM).where(
                OperationResourceRuleORM.operation_key == operation.operation_key,
                OperationResourceRuleORM.group_path == group_path,
            )
        )
        row = self.session.scalar(
            select(ResourceMonitorErrorORM).where(
                ResourceMonitorErrorORM.operation_key == operation.operation_key,
                ResourceMonitorErrorORM.group_path == group_path,
            )
        )
        if row is None:
            row = ResourceMonitorErrorORM(
                id=_new_id("resource_error"),
                operation_key=operation.operation_key,
                method=operation.method,
                path=operation.path,
                group_path=group_path,
                resource_id=rule.resource_id if rule is not None else None,
                code=warning.code,
                message=warning.message,
                issues=warning.issues,
                created_at=observed_at,
                updated_at=observed_at,
            )
            self.session.add(row)
        else:
            row.resource_id = rule.resource_id if rule is not None else None
            row.method = operation.method
            row.path = operation.path
            row.code = warning.code
            row.message = warning.message
            row.issues = warning.issues
            row.updated_at = observed_at
        self.session.flush()

    def record_operation_error(
        self,
        *,
        operation: MonitoredOperation,
        warning: ResourceMonitorWarning,
        observed_at: datetime,
    ) -> None:
        """
        Record operation error for the repository and database persistence boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        group_paths = self.session.scalars(
            select(OperationResourceRuleORM.group_path).where(
                OperationResourceRuleORM.operation_key == operation.operation_key
            )
        ).all()
        for group_path in group_paths or ["$monitor"]:
            self.record_error(
                operation=operation,
                group_path=group_path,
                warning=warning,
                observed_at=observed_at,
            )

    def clear_operation_errors(self, operation_key: str) -> None:
        """
        Handle clear operation errors as part of the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        self.session.execute(
            delete(ResourceMonitorErrorORM).where(
                ResourceMonitorErrorORM.operation_key == operation_key
            )
        )
        self.session.flush()

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult:
        """
        Look up bounded evidence used by the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        from restscope.api_behavior_monitor.resource_schemas import (
            ResourceIdentifierSummary,
            ResourceLookupResult,
            ResourceMonitorErrorSummary,
        )

        alias = self.session.scalar(
            select(ResourceAliasORM).where(
                ResourceAliasORM.normalized_alias == _normalize_name(request.resource)
            )
        )
        if alias is None:
            return ResourceLookupResult(status="not_found")
        resource = cast(
            ResourceORM | None,
            self.session.get(ResourceORM, alias.resource_id),
        )
        assert resource is not None
        aliases = self.session.scalars(
            select(ResourceAliasORM)
            .where(ResourceAliasORM.resource_id == resource.id)
            .order_by(func.lower(ResourceAliasORM.alias), ResourceAliasORM.alias)
        ).all()
        identifier_query = select(ResourceIdentifierORM).where(
            ResourceIdentifierORM.resource_id == resource.id
        )
        if request.id_value is not None:
            value_type, value_text = _encode_identifier(request.id_value)
            identifier_query = identifier_query.where(
                ResourceIdentifierORM.value_type == value_type,
                ResourceIdentifierORM.value_text == value_text,
            )
        total = self.session.scalar(
            select(func.count()).select_from(identifier_query.subquery())
        ) or 0
        identifier_rows = self.session.scalars(
            identifier_query.order_by(
                ResourceIdentifierORM.last_seen_at.desc(),
                ResourceIdentifierORM.value_type,
                ResourceIdentifierORM.value_text,
            ).limit(request.limit)
        ).all()
        selected_identifiers = identifier_rows
        operations = self._operation_summaries(
            resource_id=resource.id,
            identifier_ids=(
                [row.id for row in selected_identifiers]
                if request.id_value is not None
                else None
            ),
        )
        errors = self.session.scalars(
            select(ResourceMonitorErrorORM)
            .where(ResourceMonitorErrorORM.resource_id == resource.id)
            .order_by(ResourceMonitorErrorORM.updated_at.desc())
        ).all()
        summaries = [
            ResourceIdentifierSummary(
                value=_decode_identifier(row.value_type, row.value_text),
                value_type=row.value_type,
                last_seen_at=as_utc(row.last_seen_at),
            )
            for row in selected_identifiers
        ]
        return ResourceLookupResult(
            status="found",
            canonical_resource=resource.canonical_name,
            aliases=[row.alias for row in aliases],
            identifiers=summaries,
            recommended_id=summaries[0].value if summaries else None,
            operations=operations,
            errors=[
                ResourceMonitorErrorSummary(
                    operation_key=row.operation_key,
                    group_path=row.group_path,
                    code=row.code,
                    message=row.message,
                    issues=list(row.issues),
                    updated_at=as_utc(row.updated_at),
                )
                for row in errors
            ],
            total=total,
            truncated=total > request.limit,
        )

    def _resolve_resource(self, group: DetectedResourceGroup) -> ResourceORM:
        """
        Resolve resource for the repository and database persistence boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        assert group.resource_name is not None
        normalized_aliases = {
            _normalize_name(value)
            for value in [group.resource_name, *group.resource_aliases]
        }
        matched_resource_ids = set(
            self.session.scalars(
                select(ResourceAliasORM.resource_id).where(
                    ResourceAliasORM.normalized_alias.in_(normalized_aliases)
                )
            ).all()
        )
        canonical = self.session.scalar(
            select(ResourceORM).where(
                ResourceORM.normalized_name == _normalize_name(group.resource_name)
            )
        )
        if canonical is not None:
            matched_resource_ids.add(canonical.id)
        if len(matched_resource_ids) > 1:
            raise ResourceCatalogConflict(
                "Resource aliases resolve to multiple existing resources"
            )
        if matched_resource_ids:
            resource = cast(
                ResourceORM | None,
                self.session.get(
                    ResourceORM,
                    next(iter(matched_resource_ids)),
                ),
            )
            assert resource is not None
            return resource
        resource = ResourceORM(
            id=_new_id("resource"),
            canonical_name=group.resource_name,
            normalized_name=_normalize_name(group.resource_name),
        )
        self.session.add(resource)
        self.session.flush()
        return resource

    def _add_aliases(self, resource: ResourceORM, aliases: list[str]) -> None:
        """
        Handle add aliases as part of the repository and database persistence boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        seen: set[str] = set()
        for alias in aliases:
            normalized = _normalize_name(alias)
            if normalized in seen:
                continue
            seen.add(normalized)
            existing = self.session.scalar(
                select(ResourceAliasORM).where(
                    ResourceAliasORM.normalized_alias == normalized
                )
            )
            if existing is not None:
                if existing.resource_id != resource.id:
                    raise ResourceCatalogConflict(
                        f"Resource alias already belongs to another resource: {alias}"
                    )
                continue
            self.session.add(
                ResourceAliasORM(
                    id=_new_id("resource_alias"),
                    resource_id=resource.id,
                    alias=alias,
                    normalized_alias=normalized,
                )
            )
        self.session.flush()

    def _upsert_rule(
        self,
        *,
        resource: ResourceORM | None,
        operation: MonitoredOperation,
        group: DetectedResourceGroup,
    ) -> OperationResourceRuleORM:
        """
        Handle upsert rule as part of the repository and database persistence boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        rule = self.session.scalar(
            select(OperationResourceRuleORM).where(
                OperationResourceRuleORM.operation_key == operation.operation_key,
                OperationResourceRuleORM.group_path == group.group_path,
            )
        )
        observed = bool(group.identifier_values)
        if rule is None:
            rule = OperationResourceRuleORM(
                id=_new_id("resource_rule"),
                resource_id=resource.id if resource is not None else None,
                operation_key=operation.operation_key,
                method=operation.method,
                path=operation.path,
                group_path=group.group_path,
                has_resource=group.has_resource,
                resource_aliases=group.resource_aliases,
                id_field_name=group.id_field_name,
                id_selector=group.id_selector,
                access_mode=operation.access_mode,
                classification_source=group.classification_source,
                id_observed=observed,
            )
            self.session.add(rule)
        elif not rule.has_resource and group.has_resource:
            assert resource is not None
            rule.resource_id = resource.id
            rule.method = operation.method
            rule.path = operation.path
            rule.has_resource = True
            rule.resource_aliases = group.resource_aliases
            rule.id_field_name = group.id_field_name
            rule.id_selector = group.id_selector
            rule.access_mode = operation.access_mode
            rule.classification_source = group.classification_source
            rule.id_observed = observed
        else:
            if (
                rule.resource_id != (resource.id if resource is not None else None)
                or rule.has_resource != group.has_resource
                or rule.id_selector != group.id_selector
                or rule.id_field_name != group.id_field_name
            ):
                raise ResourceCatalogConflict(
                    f"Operation resource rule changed for {operation.operation_key} {group.group_path}"
                )
            rule.id_observed = rule.id_observed or observed
        self.session.flush()
        return rule

    def _upsert_identifier(
        self,
        *,
        resource: ResourceORM,
        value: str | int,
        observed_at: datetime,
    ) -> ResourceIdentifierORM:
        """
        Handle upsert identifier as part of the repository and database persistence
        boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        value_type, value_text = _encode_identifier(value)
        row = self.session.scalar(
            select(ResourceIdentifierORM).where(
                ResourceIdentifierORM.resource_id == resource.id,
                ResourceIdentifierORM.value_type == value_type,
                ResourceIdentifierORM.value_text == value_text,
            )
        )
        if row is None:
            row = ResourceIdentifierORM(
                id=_new_id("resource_id"),
                resource_id=resource.id,
                value_type=value_type,
                value_text=value_text,
                last_seen_at=observed_at,
            )
            self.session.add(row)
        elif observed_at > as_utc(row.last_seen_at):
            row.last_seen_at = observed_at
        self.session.flush()
        return row

    def _upsert_usage(
        self,
        *,
        identifier: ResourceIdentifierORM,
        rule: OperationResourceRuleORM,
        observed_at: datetime,
    ) -> None:
        """
        Handle upsert usage as part of the repository and database persistence boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        row = self.session.scalar(
            select(ResourceOperationUsageORM).where(
                ResourceOperationUsageORM.identifier_id == identifier.id,
                ResourceOperationUsageORM.operation_rule_id == rule.id,
            )
        )
        if row is None:
            self.session.add(
                ResourceOperationUsageORM(
                    id=_new_id("resource_usage"),
                    identifier_id=identifier.id,
                    operation_rule_id=rule.id,
                    access_mode=rule.access_mode,
                    latest_seen_at=observed_at,
                )
            )
        elif observed_at > as_utc(row.latest_seen_at):
            row.latest_seen_at = observed_at

    def _to_rule(self, row: OperationResourceRuleORM) -> LearnedResourceRule:
        """
        Handle to rule as part of the repository and database persistence boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        from restscope.api_behavior_monitor.resource_schemas import (
            LearnedResourceRule,
        )

        resource = (
            self.session.get(ResourceORM, row.resource_id)
            if row.resource_id is not None
            else None
        )
        return LearnedResourceRule.model_validate(
            {
                "rule_id": row.id,
                "resource_id": (
                    resource.id if resource is not None else None
                ),
                "has_resource": row.has_resource,
                "resource_name": (
                    resource.canonical_name
                    if resource is not None
                    else None
                ),
                "resource_aliases": list(row.resource_aliases),
                "operation": {
                    "operation_key": row.operation_key,
                    "method": row.method,
                    "path": row.path,
                },
                "group_path": row.group_path,
                "id_field_name": row.id_field_name,
                "id_selector": row.id_selector,
                "access_mode": row.access_mode,
                "classification_source": row.classification_source,
                "id_observed": row.id_observed,
            }
        )

    def _operation_summaries(
        self,
        *,
        resource_id: str,
        identifier_ids: list[str] | None,
    ) -> list[ResourceOperationSummary]:
        """
        Handle operation summaries as part of the repository and database persistence
        boundary.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        from restscope.api_behavior_monitor.resource_schemas import (
            ResourceOperationSummary,
        )

        rules = self.session.scalars(
            select(OperationResourceRuleORM).where(
                OperationResourceRuleORM.resource_id == resource_id
            )
        ).all()
        output: list[ResourceOperationSummary] = []
        for operation_key in sorted({rule.operation_key for rule in rules}):
            operation_rules = [rule for rule in rules if rule.operation_key == operation_key]
            query = select(ResourceOperationUsageORM).where(
                ResourceOperationUsageORM.operation_rule_id.in_(
                    [rule.id for rule in operation_rules]
                )
            )
            if identifier_ids is not None:
                query = query.where(
                    ResourceOperationUsageORM.identifier_id.in_(identifier_ids)
                )
            usages = self.session.scalars(query).all()
            if not usages:
                continue
            representative = operation_rules[0]
            output.append(
                ResourceOperationSummary.model_validate(
                    {
                        "operation_key": operation_key,
                        "method": representative.method,
                        "path": representative.path,
                        "access_mode": representative.access_mode,
                        "resource_aliases": sorted(
                        {
                            alias
                            for rule in operation_rules
                            for alias in rule.resource_aliases
                        },
                            key=lambda value: value.casefold(),
                        ),
                        "id_field_aliases": sorted(
                            {
                                rule.id_field_name
                                for rule in operation_rules
                                if rule.id_field_name is not None
                            },
                            key=lambda value: value.casefold(),
                        ),
                        "selectors": sorted(
                            {
                                rule.id_selector
                                for rule in operation_rules
                                if rule.id_selector is not None
                            }
                        ),
                        "latest_seen_at": max(
                            as_utc(item.latest_seen_at) for item in usages
                        ),
                    }
                )
            )
        output.sort(key=lambda item: (item.latest_seen_at, item.operation_key), reverse=True)
        return output


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not normalized:
        raise ResourceCatalogConflict("Resource name has no identifier characters")
    return normalized


def _encode_identifier(value: str | int) -> tuple[str, str]:
    if isinstance(value, bool):
        raise ResourceCatalogConflict("Boolean values are not resource identifiers")
    if isinstance(value, int):
        return "integer", str(value)
    if isinstance(value, str) and value.strip():
        return "string", value
    raise ResourceCatalogConflict("Resource identifier must be a non-empty string or integer")


def _decode_identifier(value_type: str, value_text: str) -> str | int:
    return int(value_text) if value_type == "integer" else value_text


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
