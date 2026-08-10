"""SQLAlchemy adapter for resource catalog persistence and lookup."""

from __future__ import annotations

import hashlib
import json
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
    ResourceIdentifierDefinitionORM,
    ResourceMonitorErrorORM,
    ResourceOperationUsageORM,
    ResourceORM,
)
from ..time import as_utc
from ._transaction import _SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from restscope.api_behavior_monitor.resource_identifiers.schemas import (
        DetectedResourceGroup,
        IdentifierRecord,
        LearnedResourceRule,
        MonitoredOperation,
        ResourceLookupRequest,
        ResourceLookupResult,
        ResourceMonitorWarning,
        ResourceNameSummary,
        ResourceIdentifierPage,
        ResourceIdentifierSummary,
        ResourceOperationSummary,
    )


class ResourceCatalogConflict(ValueError):
    """A learned alias or operation group contradicts existing catalog facts."""

    code = "resource_catalog_conflict"


class SqlAlchemyResourceCatalogRepository:
    """Persist resources, ordered definitions/records, rules, usage, and errors."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record_groups(
        self,
        *,
        operation: MonitoredOperation,
        groups: list[DetectedResourceGroup],
        observed_at: datetime,
    ) -> None:
        """Upsert one observation's complete definitions, records, rules, and usage."""
        for group in groups:
            if not group.has_resource:
                self._upsert_rule(
                    resource=None,
                    definition=None,
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
            definition = self._resolve_identifier_definition(
                resource=resource,
                group=group,
            )
            rule = self._upsert_rule(
                resource=resource,
                definition=definition,
                operation=operation,
                group=group,
            )
            for record in group.identifier_records:
                identifier = self._upsert_identifier(
                    definition=definition,
                    record=record,
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

    def list_rules(self, operation: MonitoredOperation) -> list[LearnedResourceRule]:
        """Load learned resource rules and aliases for one operation."""
        rows = self.session.scalars(
            select(OperationResourceRuleORM)
            .where(OperationResourceRuleORM.operation_key == operation.operation_key)
            .order_by(OperationResourceRuleORM.group_path)
        ).all()
        return [self._to_rule(row, operation=operation) for row in rows]

    def list_resources(
        self,
        *,
        limit: int | None = None,
        aliases_per_resource: int | None = None,
    ) -> list[ResourceNameSummary]:
        """Load a bounded page of canonical resource summaries and aliases."""
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
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

    def list_resource_names(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[str], int]:
        """Return canonical names and the total count without loading aliases."""
        order = (
            func.lower(ResourceORM.canonical_name),
            ResourceORM.canonical_name,
        )
        total = self.session.scalar(
            select(func.count()).select_from(ResourceORM)
        ) or 0
        names = list(
            self.session.scalars(
                select(ResourceORM.canonical_name)
                .order_by(*order)
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return names, total

    def list_identifiers(
        self,
        *,
        resource: str,
        offset: int,
        limit: int,
    ) -> ResourceIdentifierPage:
        """Resolve one name or alias and return only its typed identifier page."""
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
            ResourceIdentifierPage,
            ResourceIdentifierSummary,
        )

        alias = self.session.scalar(
            select(ResourceAliasORM).where(
                ResourceAliasORM.normalized_alias == _normalize_name(resource)
            )
        )
        if alias is None:
            return ResourceIdentifierPage(status="not_found", offset=offset)
        canonical = cast(
            ResourceORM | None,
            self.session.get(ResourceORM, alias.resource_id),
        )
        assert canonical is not None
        query = (
            select(ResourceIdentifierORM)
            .join(
                ResourceIdentifierDefinitionORM,
                ResourceIdentifierORM.definition_id == ResourceIdentifierDefinitionORM.id,
            )
            .where(ResourceIdentifierDefinitionORM.resource_id == canonical.id)
        )
        total = self.session.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        rows = self.session.scalars(
            query.order_by(
                ResourceIdentifierORM.last_seen_at.desc(),
                ResourceIdentifierORM.value_digest,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return ResourceIdentifierPage(
            status="found",
            canonical_resource=canonical.canonical_name,
            identifiers=[
                self._identifier_summary(row)
                for row in rows
            ],
            total=total,
            offset=offset,
        )

    def record_error(
        self,
        *,
        operation: MonitoredOperation,
        group_path: str,
        warning: ResourceMonitorWarning,
        observed_at: datetime,
    ) -> None:
        """Insert one bounded global resource-monitor error row."""
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
                operation_key=operation.operation_key,
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
        """Insert one bounded operation-scoped resource-monitor error row."""
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
        """Delete stale monitor errors for one operation after a successful observation."""
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
        """
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
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
        identifier_query = (
            select(ResourceIdentifierORM)
            .join(
                ResourceIdentifierDefinitionORM,
                ResourceIdentifierORM.definition_id == ResourceIdentifierDefinitionORM.id,
            )
            .where(ResourceIdentifierDefinitionORM.resource_id == resource.id)
        )
        if request.identifier is not None:
            identifier_query = identifier_query.where(
                ResourceIdentifierDefinitionORM.name == request.identifier
            )
        total = self.session.scalar(
            select(func.count()).select_from(identifier_query.subquery())
        ) or 0
        identifier_rows = self.session.scalars(
            identifier_query.order_by(
                ResourceIdentifierORM.last_seen_at.desc(),
                ResourceIdentifierORM.value_digest,
            ).limit(request.limit)
        ).all()
        selected_identifiers = identifier_rows
        operations = self._operation_summaries(
            resource_id=resource.id,
            identifier_ids=None,
        )
        errors = self.session.scalars(
            select(ResourceMonitorErrorORM)
            .where(ResourceMonitorErrorORM.resource_id == resource.id)
            .order_by(ResourceMonitorErrorORM.updated_at.desc())
        ).all()
        summaries = [
            self._identifier_summary(row)
            for row in selected_identifiers
        ]
        return ResourceLookupResult(
            status="found",
            canonical_resource=resource.canonical_name,
            aliases=[row.alias for row in aliases],
            identifiers=summaries,
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

    def _identifier_summary(
        self,
        row: ResourceIdentifierORM,
    ) -> ResourceIdentifierSummary:
        """Project a stored complete record through the Catalog Interface."""
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
            ResourceIdentifierSummary,
        )

        definition = self.session.get(
            ResourceIdentifierDefinitionORM,
            row.definition_id,
        )
        assert definition is not None
        return ResourceIdentifierSummary.model_validate(
            {
                "identifier": definition.name,
                "components": list(row.values),
                "last_seen_at": as_utc(row.last_seen_at),
            }
        )

    def _resolve_resource(self, group: DetectedResourceGroup) -> ResourceORM:
        """Resolve a canonical resource by exact name or alias, rejecting ambiguous ownership."""
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
        """Insert normalized resource aliases that are not already attached to another canonical resource."""
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
                    resource_id=resource.id,
                    alias=alias,
                    normalized_alias=normalized,
                )
            )
        self.session.flush()

    def _resolve_identifier_definition(
        self,
        *,
        resource: ResourceORM,
        group: DetectedResourceGroup,
    ) -> ResourceIdentifierDefinitionORM:
        """Reuse or create the ordered Identifier Definition selected by the Agent."""
        assert group.identifier_name is not None
        component_names = [item.component for item in group.identifier_fields]
        row = self.session.scalar(
            select(ResourceIdentifierDefinitionORM).where(
                ResourceIdentifierDefinitionORM.resource_id == resource.id,
                ResourceIdentifierDefinitionORM.name == group.identifier_name,
            )
        )
        if row is None:
            row = ResourceIdentifierDefinitionORM(
                id=_new_id("resource_identifier_definition"),
                resource_id=resource.id,
                name=group.identifier_name,
                component_names=component_names,
            )
            self.session.add(row)
        elif list(row.component_names) != component_names:
            raise ResourceCatalogConflict(
                f"Identifier Definition changed: {group.identifier_name}"
            )
        self.session.flush()
        return row

    def _upsert_rule(
        self,
        *,
        resource: ResourceORM | None,
        definition: ResourceIdentifierDefinitionORM | None,
        operation: MonitoredOperation,
        group: DetectedResourceGroup,
    ) -> OperationResourceRuleORM:
        """Insert or update the learned selector rule for one operation and resource."""
        rule = self.session.scalar(
            select(OperationResourceRuleORM).where(
                OperationResourceRuleORM.operation_key == operation.operation_key,
                OperationResourceRuleORM.group_path == group.group_path,
            )
        )
        if rule is None:
            rule = OperationResourceRuleORM(
                id=_new_id("resource_rule"),
                resource_id=resource.id if resource is not None else None,
                identifier_definition_id=(definition.id if definition is not None else None),
                operation_key=operation.operation_key,
                group_path=group.group_path,
                has_resource=group.has_resource,
                identifier_path=group.identifier_path,
                identifier_fields=[item.model_dump(mode="json") for item in group.identifier_fields],
                access_mode=operation.access_mode,
                classification_source=group.classification_source,
            )
            self.session.add(rule)
        elif not rule.has_resource and group.has_resource:
            assert resource is not None
            assert definition is not None
            rule.resource_id = resource.id
            rule.identifier_definition_id = definition.id
            rule.has_resource = True
            rule.identifier_path = group.identifier_path
            rule.identifier_fields = [item.model_dump(mode="json") for item in group.identifier_fields]
            rule.access_mode = operation.access_mode
            rule.classification_source = group.classification_source
        else:
            if (
                rule.resource_id != (resource.id if resource is not None else None)
                or rule.has_resource != group.has_resource
                or rule.identifier_definition_id != (definition.id if definition is not None else None)
                or rule.identifier_path != group.identifier_path
                or list(rule.identifier_fields) != [item.model_dump(mode="json") for item in group.identifier_fields]
            ):
                raise ResourceCatalogConflict(
                    f"Operation resource rule changed for {operation.operation_key} {group.group_path}"
                )
            rule.access_mode = operation.access_mode
            rule.classification_source = group.classification_source
        self.session.flush()
        return rule

    def _upsert_identifier(
        self,
        *,
        definition: ResourceIdentifierDefinitionORM,
        record: IdentifierRecord,
        observed_at: datetime,
    ) -> ResourceIdentifierORM:
        """Insert one complete typed Identifier Record without duplicating it."""
        values = [item.model_dump(mode="json") for item in record.components]
        value_digest = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        row = self.session.scalar(
            select(ResourceIdentifierORM).where(
                ResourceIdentifierORM.definition_id == definition.id,
                ResourceIdentifierORM.value_digest == value_digest,
            )
        )
        if row is None:
            row = ResourceIdentifierORM(
                id=_new_id("resource_id"),
                definition_id=definition.id,
                values=values,
                value_digest=value_digest,
                first_seen_at=observed_at,
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
        """Update the latest read or write usage for one resource and operation."""
        row = self.session.scalar(
            select(ResourceOperationUsageORM).where(
                ResourceOperationUsageORM.identifier_id == identifier.id,
                ResourceOperationUsageORM.operation_rule_id == rule.id,
            )
        )
        if row is None:
            self.session.add(
                ResourceOperationUsageORM(
                    identifier_id=identifier.id,
                    operation_rule_id=rule.id,
                    latest_seen_at=observed_at,
                )
            )
        elif observed_at > as_utc(row.latest_seen_at):
            row.latest_seen_at = observed_at

    def _to_rule(
        self,
        row: OperationResourceRuleORM,
        *,
        operation: MonitoredOperation,
    ) -> LearnedResourceRule:
        """Convert one ORM rule plus its aliases into the domain LearnedResourceRule."""
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
            LearnedResourceRule,
        )

        resource = (
            self.session.get(ResourceORM, row.resource_id)
            if row.resource_id is not None
            else None
        )
        aliases = (
            self.session.scalars(
                select(ResourceAliasORM.alias)
                .where(ResourceAliasORM.resource_id == row.resource_id)
                .order_by(ResourceAliasORM.normalized_alias)
            ).all()
            if row.resource_id is not None
            else []
        )
        id_observed = self.session.scalar(
            select(ResourceOperationUsageORM.identifier_id)
            .where(ResourceOperationUsageORM.operation_rule_id == row.id)
            .limit(1)
        ) is not None
        definition = (
            self.session.get(ResourceIdentifierDefinitionORM, row.identifier_definition_id)
            if row.identifier_definition_id is not None
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
                "resource_aliases": list(aliases),
                "operation": operation.model_dump(mode="json"),
                "group_path": row.group_path,
                "identifier_name": definition.name if definition is not None else None,
                "identifier_path": row.identifier_path,
                "identifier_fields": list(row.identifier_fields),
                "access_mode": row.access_mode,
                "classification_source": row.classification_source,
                "id_observed": id_observed,
            }
        )

    def _operation_summaries(
        self,
        *,
        resource_id: str,
        identifier_ids: list[str] | None,
    ) -> list[ResourceOperationSummary]:
        """Project resource operation rows into stable read/write summaries."""
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
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
            aliases = self.session.scalars(
                select(ResourceAliasORM.alias)
                .where(ResourceAliasORM.resource_id == resource_id)
                .order_by(ResourceAliasORM.normalized_alias)
            ).all()
            output.append(
                ResourceOperationSummary.model_validate(
                    {
                        "operation_key": operation_key,
                        "access_mode": representative.access_mode,
                        "resource_aliases": list(aliases),
                        "id_field_aliases": sorted(
                            {
                                field["field_name"]
                                for rule in operation_rules
                                for field in rule.identifier_fields
                            },
                            key=lambda value: value.casefold(),
                        ),
                        "selectors": sorted(
                            {
                                field["selector"]
                                for rule in operation_rules
                                for field in rule.identifier_fields
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


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SqlAlchemyResourceCatalogUnitOfWork(_SqlAlchemyUnitOfWork):
    """Open one transaction for learned Resource Identifier evidence."""

    def __enter__(self) -> "SqlAlchemyResourceCatalogUnitOfWork":
        """Bind the Resource repository to a newly opened session."""

        self.resources = SqlAlchemyResourceCatalogRepository(self._open_session())
        return self
