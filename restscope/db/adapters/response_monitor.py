"""SQLAlchemy adapter for API Response Monitor facts and derived state.

This Module translates the Catalog's immutable records into ORM rows.  It owns
the mechanical latest-one-hundred observation retention rule but does not
interpret response JSON, classify resources, or choose Generator sources.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from restscope.api_behavior_monitor.catalog import (
    AbstractTestCaseRecord,
    AbstractTestCaseWrite,
    ObservationRecord,
    ObservationWrite,
    ObservedResponseCoordinate,
    OperationDefinition,
    OperationInputSource,
    ResourceDefinitionRecord,
    ResourceDerivation,
    ResourceDerivationResult,
    ResourceInstanceRecord,
    merge_resource_state,
    resource_instance_id,
)

from ..orm import (
    AbstractTestCaseORM,
    ObservationORM,
    OperationInputSourceORM,
    OperationORM,
    OperationResourceEdgeORM,
    ResourceInstanceORM,
    ResourceORM,
)
from ..time import as_utc
from ._transaction import _SqlAlchemyUnitOfWork


class SqlAlchemyResponseMonitorRepository:
    """Store Response Monitor rows in one caller-owned SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        """Retain the session without committing it independently."""

        self.session = session

    def ensure_operation(
        self,
        operation: OperationDefinition,
    ) -> OperationDefinition:
        """Insert one operation or refresh only its mutable description."""

        row = self.session.get(OperationORM, operation.operation_id)
        if row is None:
            row = OperationORM(
                operation_id=operation.operation_id,
                method=operation.method,
                path=operation.path,
                description=operation.description,
            )
            self.session.add(row)
        elif row.method != operation.method or row.path != operation.path:
            # Operation IDs are structural identities. Treat disagreement as
            # corrupted input instead of silently rewriting historical keys.
            raise ValueError("operation identity conflicts with the stored operation")
        else:
            row.description = operation.description
        self.session.flush()
        return _operation_definition(row)

    def record_observation(
        self,
        observation: ObservationWrite,
    ) -> ObservationRecord:
        """Insert one exact response and delete rows older than the newest hundred."""

        row = ObservationORM(
            observation_id=f"observation_{uuid4().hex}",
            operation_id=observation.operation_id,
            timestamp=observation.timestamp,
            status_code=observation.status_code,
            media_type=observation.media_type,
            request_json=deepcopy(observation.request_json),
            response_json=observation.response_json,
            abstract_test_case_id=observation.abstract_test_case_id,
        )
        self.session.add(row)
        self.session.flush()

        # Ordering by the generated identity resolves responses whose receipt
        # timestamps are equal without relying on database insertion order.
        expired_ids = self.session.scalars(
            select(ObservationORM.observation_id)
            .where(ObservationORM.operation_id == observation.operation_id)
            .order_by(
                ObservationORM.timestamp.desc(),
                ObservationORM.observation_id.desc(),
            )
            .offset(100)
        ).all()
        if expired_ids:
            self.session.execute(
                delete(ObservationORM).where(
                    ObservationORM.observation_id.in_(expired_ids)
                )
            )
        return _observation_record(row)

    def list_observations(
        self,
        *,
        operation_id: str,
        status_code: int | None,
        media_type: str | None,
        offset: int,
        limit: int,
    ) -> list[ObservationRecord]:
        """Return one filtered deterministic newest-first observation page."""

        query = select(ObservationORM).where(
            ObservationORM.operation_id == operation_id
        )
        if status_code is not None:
            query = query.where(ObservationORM.status_code == status_code)
        if media_type is not None:
            query = query.where(ObservationORM.media_type == media_type)
        rows = self.session.scalars(
            query
            .order_by(
                ObservationORM.timestamp.desc(),
                ObservationORM.observation_id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [_observation_record(row) for row in rows]

    def list_observed_response_coordinates(
        self,
    ) -> list[ObservedResponseCoordinate]:
        """Return distinct retained coordinates without reading response bodies."""

        rows = self.session.execute(
            select(
                ObservationORM.operation_id,
                ObservationORM.status_code,
                ObservationORM.media_type,
            )
            .distinct()
            .order_by(
                ObservationORM.operation_id,
                ObservationORM.status_code,
                ObservationORM.media_type,
            )
        ).all()
        return [
            ObservedResponseCoordinate(
                operation_key=operation_id,
                status_code=status_code,
                media_type=media_type,
            )
            for operation_id, status_code, media_type in rows
        ]

    def record_resource_derivations(
        self,
        *,
        operation_id: str,
        derivations: list[ResourceDerivation],
    ) -> ResourceDerivationResult:
        """Write resources and instances while isolating immutable identity conflicts."""

        resources: list[ResourceDefinitionRecord] = []
        instances: list[ResourceInstanceRecord] = []
        conflicts: list[str] = []
        for derivation in derivations:
            resource = self.session.scalar(
                select(ResourceORM).where(ResourceORM.name == derivation.resource_name)
            )
            if resource is None:
                resource = ResourceORM(
                    resource_id=f"resource_{uuid4().hex}",
                    name=derivation.resource_name,
                    identity_fields=list(derivation.identity_fields),
                )
                self.session.add(resource)
                self.session.flush()
            elif tuple(resource.identity_fields) != tuple(derivation.identity_fields):
                conflicts.append(derivation.resource_name)
                continue

            edge_key = (
                operation_id,
                resource.resource_id,
                derivation.role,
            )
            if self.session.get(OperationResourceEdgeORM, edge_key) is None:
                self.session.add(
                    OperationResourceEdgeORM(
                        operation_id=operation_id,
                        resource_id=resource.resource_id,
                        role=derivation.role,
                        _alpha=1,
                        _beta=1,
                    )
                )
                # The shared session disables autoflush. Materialize this
                # natural key now so a later derivation in the same response
                # can observe and reuse it instead of scheduling a duplicate.
                self.session.flush()

            resources.append(_resource_definition(resource))
            for observed in derivation.instances:
                instance_key = resource_instance_id(
                    derivation.identity_fields,
                    observed,
                )
                row = self.session.get(
                    ResourceInstanceORM,
                    (resource.name, instance_key),
                )
                merged = merge_resource_state(
                    row.current_state_json if row is not None else None,
                    observed,
                    deleted=derivation.role == "DELETED",
                )
                if row is None:
                    row = ResourceInstanceORM(
                        resource_type=resource.name,
                        resource_instance_id=instance_key,
                        current_state_json=merged,
                    )
                    self.session.add(row)
                else:
                    row.current_state_json = merged
                # Repeated appearances of the same instance must merge in
                # response order, including across normalized derivations.
                self.session.flush()
                instances.append(_resource_instance_record(row))
        return ResourceDerivationResult(
            resources=tuple(resources),
            instances=tuple(instances),
            conflicts=tuple(conflicts),
        )

    def list_resources(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceDefinitionRecord], int]:
        """Return an alphabetical page and the complete resource count."""

        total = self.session.scalar(select(func.count()).select_from(ResourceORM)) or 0
        rows = self.session.scalars(
            select(ResourceORM).order_by(ResourceORM.name).offset(offset).limit(limit)
        ).all()
        return [_resource_definition(row) for row in rows], total

    def get_resource(self, *, name: str) -> ResourceDefinitionRecord | None:
        """Read one resource by its unique normalized database key."""

        row = self.session.scalar(select(ResourceORM).where(ResourceORM.name == name))
        return None if row is None else _resource_definition(row)

    def list_resource_instances(
        self,
        *,
        resource_type: str,
        offset: int,
        limit: int,
        include_deleted: bool,
    ) -> tuple[list[ResourceInstanceRecord], int]:
        """Return exact-type instances, hiding lifecycle-deleted rows by default."""

        query = select(ResourceInstanceORM).where(
            ResourceInstanceORM.resource_type == resource_type
        )
        if not include_deleted:
            query = query.where(
                ResourceInstanceORM.current_state_json["_deleted"].as_boolean()
                == False  # noqa: E712 - SQLAlchemy overloads comparison.
            )
        count_query = select(func.count()).select_from(query.subquery())
        total = self.session.scalar(count_query) or 0
        rows = self.session.scalars(
            query.order_by(ResourceInstanceORM.resource_instance_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_resource_instance_record(row) for row in rows], total

    def list_operation_resources(
        self,
        *,
        operation_id: str,
    ) -> list[ResourceDefinitionRecord]:
        """Return distinct resource definitions connected to one operation."""

        rows = self.session.scalars(
            select(ResourceORM)
            .join(
                OperationResourceEdgeORM,
                OperationResourceEdgeORM.resource_id == ResourceORM.resource_id,
            )
            .where(OperationResourceEdgeORM.operation_id == operation_id)
            .distinct()
            .order_by(ResourceORM.name)
        ).all()
        return [_resource_definition(row) for row in rows]

    def ensure_input_source(
        self,
        source: OperationInputSource,
    ) -> OperationInputSource:
        """Insert one exact source key or return its existing neutral record."""

        key = (
            source.consumer_operation_id,
            source.consumer_input_node_id,
            source.producer_operation_id,
            source.status_code,
            source.media_type,
            source.selector,
            source.field_name,
            source.consume_type,
        )
        row = self.session.get(OperationInputSourceORM, key)
        if row is None:
            row = OperationInputSourceORM(
                consumer_operation_id=source.consumer_operation_id,
                consumer_input_node_id=source.consumer_input_node_id,
                producer_operation_id=source.producer_operation_id,
                status_code=source.status_code,
                media_type=source.media_type,
                selector=source.selector,
                field_name=source.field_name,
                consume_type=source.consume_type,
                _alpha=source.alpha,
                _beta=source.beta,
            )
            self.session.add(row)
            self.session.flush()
        return _input_source(row)

    def list_input_sources(
        self,
        *,
        consumer_operation_id: str,
        consumer_input_node_id: str,
    ) -> list[OperationInputSource]:
        """Return historical source meanings in stable producer order."""

        rows = self.session.scalars(
            select(OperationInputSourceORM)
            .where(
                OperationInputSourceORM.consumer_operation_id
                == consumer_operation_id,
                OperationInputSourceORM.consumer_input_node_id
                == consumer_input_node_id,
            )
            .order_by(
                OperationInputSourceORM.producer_operation_id,
                OperationInputSourceORM.status_code,
                OperationInputSourceORM.media_type,
                OperationInputSourceORM.selector,
                OperationInputSourceORM.consume_type,
            )
        ).all()
        return [_input_source(row) for row in rows]

    def ensure_abstract_test_case(
        self,
        test_case: AbstractTestCaseWrite,
    ) -> AbstractTestCaseRecord:
        """Insert one immutable state snapshot or verify its existing content."""

        row = self.session.scalar(
            select(AbstractTestCaseORM).where(
                AbstractTestCaseORM.operation_id == test_case.operation_id,
                AbstractTestCaseORM.state_digest == test_case.state_digest,
            )
        )
        if row is None:
            row = AbstractTestCaseORM(
                abstract_test_case_id=f"abstract_test_case_{uuid4().hex}",
                operation_id=test_case.operation_id,
                state_digest=test_case.state_digest,
                generators_json=deepcopy(test_case.generators_json),
                constraints_json=deepcopy(test_case.constraints_json),
            )
            self.session.add(row)
            self.session.flush()
        elif (
            row.generators_json != test_case.generators_json
            or row.constraints_json != test_case.constraints_json
        ):
            # A digest collision or caller bug must never rewrite the immutable
            # historical meaning of an existing test-case identity.
            raise ValueError("abstract test-case digest conflicts with stored content")
        return _abstract_test_case(row)


def _operation_definition(row: OperationORM) -> OperationDefinition:
    """Detach one operation row from ORM-managed state."""

    return OperationDefinition(
        operation_id=row.operation_id,
        method=row.method,
        path=row.path,
        description=row.description,
    )


def _observation_record(row: ObservationORM) -> ObservationRecord:
    """Detach one observation row while preserving its original JSON text."""

    return ObservationRecord(
        observation_id=row.observation_id,
        operation_id=row.operation_id,
        timestamp=as_utc(row.timestamp),
        status_code=row.status_code,
        media_type=row.media_type,
        request_json=deepcopy(row.request_json),
        response_json=row.response_json,
        abstract_test_case_id=row.abstract_test_case_id,
    )


def _resource_definition(row: ResourceORM) -> ResourceDefinitionRecord:
    """Detach one resource definition from its ORM row."""

    return ResourceDefinitionRecord(
        resource_id=row.resource_id,
        name=row.name,
        identity_fields=tuple(row.identity_fields),
    )


def _resource_instance_record(row: ResourceInstanceORM) -> ResourceInstanceRecord:
    """Detach one current resource state from its ORM row."""

    return ResourceInstanceRecord(
        resource_type=row.resource_type,
        resource_instance_id=row.resource_instance_id,
        current_state_json=deepcopy(row.current_state_json),
    )


def _input_source(row: OperationInputSourceORM) -> OperationInputSource:
    """Detach one source row without interpreting its frozen Beta counts."""

    return OperationInputSource(
        consumer_operation_id=row.consumer_operation_id,
        consumer_input_node_id=row.consumer_input_node_id,
        producer_operation_id=row.producer_operation_id,
        status_code=row.status_code,
        media_type=row.media_type,
        selector=row.selector,
        field_name=row.field_name,
        consume_type=row.consume_type,
        alpha=row._alpha,
        beta=row._beta,
    )


def _abstract_test_case(row: AbstractTestCaseORM) -> AbstractTestCaseRecord:
    """Detach one immutable abstract test-case snapshot from ORM state."""

    return AbstractTestCaseRecord(
        abstract_test_case_id=row.abstract_test_case_id,
        operation_id=row.operation_id,
        state_digest=row.state_digest,
        generators_json=deepcopy(row.generators_json),
        constraints_json=deepcopy(row.constraints_json),
        created_at=as_utc(row.created_at),
    )


class SqlAlchemyResponseMonitorUnitOfWork(_SqlAlchemyUnitOfWork):
    """Open one transaction containing the unified Response Monitor repository."""

    def __enter__(self) -> "SqlAlchemyResponseMonitorUnitOfWork":
        """Open the session and expose its repository."""

        self.response_monitor = SqlAlchemyResponseMonitorRepository(
            self._open_session()
        )
        return self
