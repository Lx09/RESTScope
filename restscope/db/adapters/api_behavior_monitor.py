"""SQLAlchemy adapter for API Behavior Monitor evidence and audit state.

This Module translates the Catalog's immutable records into ORM rows. It keeps
complete Batch and executed-request evidence without interpreting response
bodies, classifying resources, or choosing Generator sources.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Self
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from restscope.api_behavior_monitor.catalog import (
    AbstractTestCaseRecord,
    AbstractTestCaseWrite,
    BatchRecord,
    BatchWrite,
    ObservationRecord,
    ObservationWrite,
    ObservedResponseCoordinate,
    OpenAPIChangeEventRecord,
    OpenAPIChangeEventWrite,
    OperationDefinition,
    OperationInputSource,
    OperationTestProgress,
    OracleAssessment,
    OracleAssessmentRecord,
    ResourceDefinitionRecord,
    ResourceDerivation,
    ResourceDerivationResult,
    ResourceInstanceRecord,
    ResourceStateContext,
    ResourceStateCount,
    ResourceStateEventRecord,
    TestProgressSnapshot,
    merge_resource_state,
    resource_instance_id,
)

from ..orm import (
    AbstractTestCaseORM,
    BatchORM,
    ObservationORM,
    OpenAPIChangeEventORM,
    OpenAPICurrentORM,
    OperationInputSourceORM,
    OperationORM,
    OperationResourceEdgeORM,
    OracleAssessmentORM,
    ResourceInstanceORM,
    ResourceORM,
    ResourceStateEventORM,
)
from ..time import as_utc, utc_now
from ._transaction import _SqlAlchemyUnitOfWork


class _SqlAlchemyAPIBehaviorRepository:
    """Store all API Behavior rows in one caller-owned SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        """Retain the session without committing it independently."""

        self.session = session

    def initialize_api(
        self,
        *,
        document: dict[str, object],
        operations: list[OperationDefinition],
    ) -> None:
        """Insert the initial document and every normalized operation atomically."""

        if self.session.get(OpenAPICurrentORM, 1) is not None:
            raise ValueError("The API Behavior Catalog is already initialized")
        self.session.add(
            OpenAPICurrentORM(
                singleton_id=1,
                document=deepcopy(document),
            )
        )
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError(
                "The API Behavior Catalog is already initialized"
            ) from exc
        for operation in operations:
            self.ensure_operation(operation)

    def get_current_openapi(self) -> dict[str, object] | None:
        """Return the current normalized OpenAPI document detached from ORM state."""

        row = self.session.get(OpenAPICurrentORM, 1)
        return deepcopy(row.document) if row is not None else None

    def record_openapi_change(
        self,
        *,
        document: dict[str, object],
        event: OpenAPIChangeEventWrite,
    ) -> OpenAPIChangeEventRecord:
        """Replace the current document and append its Contract change event."""

        current = self.session.get(OpenAPICurrentORM, 1)
        if current is None:
            raise RuntimeError("The API Behavior Catalog has not been initialized")
        current.document = deepcopy(document)
        row = OpenAPIChangeEventORM(
            id=f"openapi_change_{uuid4().hex}",
            operation_id=event.operation_key,
            status_code=event.status_code,
            media_type=event.media_type,
            changes=list(event.changes),
            response_before=deepcopy(event.response_before),
            response_after=deepcopy(event.response_after),
        )
        self.session.add(row)
        self.session.flush()
        return _openapi_change_record(row)

    def list_openapi_changes(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return Contract change events in durable creation order."""

        query = select(OpenAPIChangeEventORM)
        if operation_key is not None:
            query = query.where(OpenAPIChangeEventORM.operation_id == operation_key)
        rows = self.session.scalars(
            query.order_by(OpenAPIChangeEventORM.created_at, OpenAPIChangeEventORM.id)
        ).all()
        return [_openapi_change_record(row) for row in rows]

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

    def create_batch(self, batch: BatchWrite) -> BatchRecord:
        """Insert one Batch identity with its complete initial summary."""

        row = BatchORM(
            batch_id=f"batch_{uuid4().hex}",
            summary=deepcopy(batch.summary),
        )
        self.session.add(row)
        self.session.flush()
        return _batch_record(row)

    def update_batch_summary(
        self,
        *,
        batch_id: str,
        summary: dict[str, object],
    ) -> BatchRecord | None:
        """Replace a known Batch's summary without creating missing identities."""

        row = self.session.get(BatchORM, batch_id)
        if row is None:
            return None
        row.summary = deepcopy(summary)
        self.session.flush()
        return _batch_record(row)

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        """Return one exact Batch record when it exists."""

        row = self.session.get(BatchORM, batch_id)
        return _batch_record(row) if row is not None else None

    def record_observation(
        self,
        observation: ObservationWrite,
    ) -> ObservationRecord:
        """Insert one exact HTTP or transport outcome without retention pruning."""

        row = ObservationORM(
            observation_id=f"observation_{uuid4().hex}",
            operation_id=observation.operation_id,
            timestamp=observation.timestamp,
            outcome_kind=observation.outcome_kind,
            status_code=observation.status_code,
            reason_phrase=observation.reason_phrase,
            media_type=observation.media_type,
            request_json=deepcopy(observation.request_json),
            response_headers=deepcopy(observation.response_headers),
            response_body=observation.response_body,
            body_format=observation.body_format,
            transport_code=observation.transport_code,
            transport_message=observation.transport_message,
            abstract_test_case_id=observation.abstract_test_case_id,
            batch_id=observation.batch_id,
            batch_case_index=observation.batch_case_index,
            replay_of_observation_id=observation.replay_of_observation_id,
        )
        if observation.replay_of_observation_id is not None:
            primary = self.session.get(
                ObservationORM,
                observation.replay_of_observation_id,
            )
            if primary is None:
                raise ValueError("Replay Primary Observation does not exist")
            if primary.replay_of_observation_id is not None:
                raise ValueError("A Replay cannot be the Primary of another Replay")
            if primary.operation_id != observation.operation_id:
                raise ValueError("Replay and Primary operations must match")
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError("Observation identity or Replay lineage conflicts") from exc
        return _observation_record(row)

    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        """Return one exact executed request result by durable identity."""

        row = self.session.get(ObservationORM, observation_id)
        return _observation_record(row) if row is not None else None

    def record_oracle_assessment(
        self,
        *,
        primary_observation_id: str,
        replay_observation_id: str | None,
        assessment: OracleAssessment,
    ) -> OracleAssessmentRecord:
        """Insert one immutable Assessment after validating factual lineage."""

        primary = self.session.get(ObservationORM, primary_observation_id)
        if primary is None or primary.outcome_kind != "http":
            raise ValueError("Oracle Assessment requires a Primary HTTP Observation")
        if primary.replay_of_observation_id is not None:
            raise ValueError("A Replay cannot own an Oracle Assessment")
        if replay_observation_id is not None:
            replay = self.session.get(ObservationORM, replay_observation_id)
            if replay is None or replay.replay_of_observation_id != primary_observation_id:
                raise ValueError("Assessment Replay does not belong to its Primary")
        row = OracleAssessmentORM(
            primary_observation_id=primary_observation_id,
            replay_observation_id=replay_observation_id,
            is_bug=assessment.is_bug,
            assessment_json=assessment.model_dump(mode="json"),
            completed_at=utc_now(),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise ValueError("Oracle Assessment is immutable or Replay is reused") from exc
        return _oracle_assessment_record(row)

    def get_oracle_assessment(
        self,
        primary_observation_id: str,
    ) -> OracleAssessmentRecord | None:
        """Return one detached final Assessment by Primary identity."""

        row = self.session.get(OracleAssessmentORM, primary_observation_id)
        return _oracle_assessment_record(row) if row is not None else None

    def list_batch_observations(
        self,
        *,
        batch_id: str,
        offset: int,
        limit: int,
    ) -> tuple[list[ObservationRecord], int]:
        """Return one Batch page in its original zero-based Case order."""

        predicate = ObservationORM.batch_id == batch_id
        total = self.session.scalar(
            select(func.count()).select_from(ObservationORM).where(predicate)
        ) or 0
        rows = self.session.scalars(
            select(ObservationORM)
            .where(predicate)
            .order_by(
                ObservationORM.batch_case_index,
                ObservationORM.observation_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [_observation_record(row) for row in rows], total

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
            ObservationORM.operation_id == operation_id,
            ObservationORM.outcome_kind == "http",
            ObservationORM.status_code.between(200, 299),
            ObservationORM.body_format == "json",
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
            .where(
                ObservationORM.outcome_kind == "http",
                ObservationORM.status_code.between(200, 299),
                ObservationORM.body_format == "json",
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
        observation_id: str,
        derivations: list[ResourceDerivation],
    ) -> ResourceDerivationResult:
        """Write one Observation's resource facts and final transitions atomically."""

        observation = self.session.get(ObservationORM, observation_id)
        if observation is None or observation.operation_id != operation_id:
            raise ValueError("resource state Observation must exist for the operation")

        resources: dict[str, ResourceDefinitionRecord] = {}
        instances: dict[tuple[str, str], ResourceInstanceORM] = {}
        previous_states: dict[tuple[str, str], str | None] = {}
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

            edge_key = (operation_id, resource.resource_id)
            edge = self.session.get(OperationResourceEdgeORM, edge_key)
            if edge is None:
                self.session.add(
                    OperationResourceEdgeORM(
                        operation_id=operation_id,
                        resource_id=resource.resource_id,
                        role=derivation.role,
                        result_state=derivation.result_state,
                        _alpha=1,
                        _beta=1,
                    )
                )
                # The shared session disables autoflush. Materialize this
                # natural key now so a later derivation in the same response
                # can observe and reuse it instead of scheduling a duplicate.
                self.session.flush()
            elif (
                edge.role != derivation.role
                or edge.result_state != derivation.result_state
            ):
                raise ValueError(
                    "operation resource role and result state are immutable"
                )

            resources[resource.name] = _resource_definition(resource)
            for observed in derivation.instances:
                instance_key = resource_instance_id(
                    derivation.identity_fields,
                    observed,
                )
                row = self.session.get(
                    ResourceInstanceORM,
                    (resource.name, instance_key),
                )
                transition_key = (resource.name, instance_key)
                previous_states.setdefault(
                    transition_key,
                    row.semantic_state if row is not None else None,
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
                        semantic_state=derivation.result_state,
                    )
                    self.session.add(row)
                else:
                    row.current_state_json = merged
                    row.semantic_state = derivation.result_state
                # Repeated appearances of the same instance must merge in
                # response order, including across normalized derivations.
                self.session.flush()
                instances[transition_key] = row

        # One Observation may repeat an instance in several response groups.
        # Compare its pre-response state with the final assigned state and emit
        # at most one causal transition after all recursive merges finish.
        for transition_key, row in instances.items():
            previous_state = previous_states[transition_key]
            if previous_state == row.semantic_state:
                continue
            self.session.add(
                ResourceStateEventORM(
                    event_id=f"resource_state_event_{uuid4().hex}",
                    resource_type=row.resource_type,
                    resource_instance_id=row.resource_instance_id,
                    previous_state=previous_state,
                    current_state=row.semantic_state,
                    observation_id=observation_id,
                )
            )
        self.session.flush()
        return ResourceDerivationResult(
            resources=tuple(resources.values()),
            instances=tuple(
                _resource_instance_record(row) for row in instances.values()
            ),
            conflicts=tuple(conflicts),
        )

    def read_resource_state_contexts(
        self,
        *,
        operation_id: str,
        resource_names: tuple[str, ...],
    ) -> list[ResourceStateContext]:
        """Read current edge mappings and the complete established vocabulary."""

        vocabulary_rows = self.session.execute(
            select(ResourceORM.name, OperationResourceEdgeORM.result_state)
            .join(
                OperationResourceEdgeORM,
                OperationResourceEdgeORM.resource_id == ResourceORM.resource_id,
            )
            .where(ResourceORM.name.in_(resource_names))
            .distinct()
        ).all()
        current_rows = self.session.execute(
            select(ResourceORM.name, OperationResourceEdgeORM.result_state)
            .join(
                OperationResourceEdgeORM,
                OperationResourceEdgeORM.resource_id == ResourceORM.resource_id,
            )
            .where(
                ResourceORM.name.in_(resource_names),
                OperationResourceEdgeORM.operation_id == operation_id,
            )
        ).all()
        vocabulary: dict[str, set[str]] = {name: set() for name in resource_names}
        for resource_name, result_state in vocabulary_rows:
            vocabulary[resource_name].add(result_state)
        current = dict(current_rows)
        return [
            ResourceStateContext(
                resource_name=name,
                operation_result_state=current.get(name),
                existing_states=tuple(sorted(vocabulary[name])),
            )
            for name in resource_names
        ]

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
                == False
            )
        count_query = select(func.count()).select_from(query.subquery())
        total = self.session.scalar(count_query) or 0
        rows = self.session.scalars(
            query.order_by(ResourceInstanceORM.resource_instance_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_resource_instance_record(row) for row in rows], total

    def list_resource_state_events(
        self,
        *,
        resource_type: str,
        resource_instance_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[ResourceStateEventRecord], int]:
        """Return chronological transitions joined to their stored Test Case facts."""

        where = [ResourceStateEventORM.resource_type == resource_type]
        if resource_instance_id is not None:
            where.append(
                ResourceStateEventORM.resource_instance_id == resource_instance_id
            )
        total = self.session.scalar(
            select(func.count()).select_from(ResourceStateEventORM).where(*where)
        ) or 0
        rows = self.session.execute(
            select(ResourceStateEventORM, ObservationORM)
            .join(
                ObservationORM,
                ObservationORM.observation_id == ResourceStateEventORM.observation_id,
            )
            .where(*where)
            .order_by(
                ResourceStateEventORM.created_at,
                ResourceStateEventORM.event_id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            _resource_state_event_record(event, observation)
            for event, observation in rows
        ], total

    def read_test_progress(self) -> TestProgressSnapshot:
        """Aggregate operation Batch/case counts and current resource states."""

        operations = self.session.scalars(
            select(OperationORM).order_by(OperationORM.operation_id)
        ).all()
        case_counts: dict[tuple[str, str], int] = {}
        batch_counts: dict[tuple[str, str], int] = {}
        for batch in self.session.scalars(select(BatchORM)).all():
            summary = batch.summary
            if summary.get("schema_version") != 1:
                continue
            batch_status = summary.get("status")
            operation_key = summary.get("operation_key")
            test_mode = summary.get("test_mode")
            executed = summary.get("executed_case_count")
            if (
                batch_status not in {"running", "failed", "completed"}
                or not isinstance(operation_key, str)
                or test_mode not in {"happy_path", "exceptional"}
                or isinstance(executed, bool)
                or not isinstance(executed, int)
                or executed < 0
            ):
                continue
            key = (operation_key, test_mode)
            # A zero-case failed or running Batch is still an attempted Batch.
            # Its executed-case contribution correctly remains zero.
            batch_counts[key] = batch_counts.get(key, 0) + 1
            case_counts[key] = case_counts.get(key, 0) + executed

        state_rows = self.session.execute(
            select(
                ResourceInstanceORM.resource_type,
                ResourceInstanceORM.semantic_state,
                func.count(),
            )
            .group_by(
                ResourceInstanceORM.resource_type,
                ResourceInstanceORM.semantic_state,
            )
            .order_by(
                ResourceInstanceORM.resource_type,
                ResourceInstanceORM.semantic_state,
            )
        ).all()
        return TestProgressSnapshot(
            operations=tuple(
                OperationTestProgress(
                    operation_id=row.operation_id,
                    method=row.method,
                    path=row.path,
                    positive_batch_count=batch_counts.get(
                        (row.operation_id, "happy_path"),
                        0,
                    ),
                    negative_batch_count=batch_counts.get(
                        (row.operation_id, "exceptional"),
                        0,
                    ),
                    positive_case_count=case_counts.get(
                        (row.operation_id, "happy_path"),
                        0,
                    ),
                    negative_case_count=case_counts.get(
                        (row.operation_id, "exceptional"),
                        0,
                    ),
                )
                for row in operations
            ),
            resource_states=tuple(
                ResourceStateCount(
                    resource_type=resource_type,
                    semantic_state=semantic_state,
                    instance_count=instance_count,
                )
                for resource_type, semantic_state, instance_count in state_rows
            ),
        )

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


def _batch_record(row: BatchORM) -> BatchRecord:
    """Detach one Batch summary from ORM-managed state."""

    return BatchRecord(
        batch_id=row.batch_id,
        summary=deepcopy(row.summary),
    )


def _openapi_change_record(
    row: OpenAPIChangeEventORM,
) -> OpenAPIChangeEventRecord:
    """Detach one OpenAPI Contract change from its ORM row."""

    return OpenAPIChangeEventRecord(
        id=row.id,
        operation_key=row.operation_id or "",
        status_code=row.status_code,
        media_type=row.media_type,
        changes=list(row.changes),
        response_before=deepcopy(row.response_before),
        response_after=deepcopy(row.response_after),
        created_at=as_utc(row.created_at),
    )


def _observation_record(row: ObservationORM) -> ObservationRecord:
    """Detach one complete observation row from ORM-managed state."""

    return ObservationRecord(
        observation_id=row.observation_id,
        operation_id=row.operation_id,
        timestamp=as_utc(row.timestamp),
        outcome_kind=row.outcome_kind,
        status_code=row.status_code,
        reason_phrase=row.reason_phrase,
        media_type=row.media_type,
        request_json=deepcopy(row.request_json),
        response_headers=deepcopy(row.response_headers),
        response_body=row.response_body,
        body_format=row.body_format,
        transport_code=row.transport_code,
        transport_message=row.transport_message,
        abstract_test_case_id=row.abstract_test_case_id,
        batch_id=row.batch_id,
        batch_case_index=row.batch_case_index,
        replay_of_observation_id=row.replay_of_observation_id,
    )


def _oracle_assessment_record(row: OracleAssessmentORM) -> OracleAssessmentRecord:
    """Detach and validate one stored Assessment before returning it."""

    return OracleAssessmentRecord(
        primary_observation_id=row.primary_observation_id,
        replay_observation_id=row.replay_observation_id,
        is_bug=row.is_bug,
        assessment=OracleAssessment.model_validate(row.assessment_json),
        completed_at=as_utc(row.completed_at),
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
        semantic_state=row.semantic_state,
    )


def _resource_state_event_record(
    row: ResourceStateEventORM,
    observation: ObservationORM,
) -> ResourceStateEventRecord:
    """Detach one transition while deriving operation and Batch causality."""

    return ResourceStateEventRecord(
        event_id=row.event_id,
        resource_type=row.resource_type,
        resource_instance_id=row.resource_instance_id,
        previous_state=row.previous_state,
        current_state=row.current_state,
        observation_id=row.observation_id,
        operation_id=observation.operation_id,
        batch_id=observation.batch_id,
        batch_case_index=observation.batch_case_index,
        created_at=as_utc(row.created_at),
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


class SqlAlchemyAPIBehaviorUnitOfWork(_SqlAlchemyUnitOfWork):
    """Open one transaction containing the complete API Behavior repository."""

    def __enter__(self) -> Self:
        """Open the session and expose its repository."""

        self.api_behavior = _SqlAlchemyAPIBehaviorRepository(self._open_session())
        return self
