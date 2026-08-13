"""Map all durable API Behavior Monitor evidence and audit state.

The API Behavior Monitor initializes the current normalized OpenAPI and its
operations, audits Contract changes, and writes every matched HTTP or transport
Observation. Complete valid 2xx JSON Observations may additionally derive
resources, current instances, and exact request-input sources. Request
Generation records durable Batch summaries and immutable abstract configuration.
These tables never store Agent reasoning, scheduler state, or a restorable
Generation State.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class OpenAPICurrentORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the one normalized OpenAPI document owned by the current App."""

    __tablename__ = "openapi_current"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="singleton_id_is_one"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class OpenAPIChangeEventORM(CreatedAtMixin, Base):
    """Map one append-only response-contract change caused by an observation."""

    __tablename__ = "openapi_change_events"
    __table_args__ = (
        Index(
            "ix_openapi_change_events_operation_created",
            "operation_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    response_before: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    response_after: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class OperationORM(Base):
    """Map one normalized OpenAPI operation used by a runtime response."""

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("method", "path", name="operation_method_path"),
    )

    operation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResourceORM(Base):
    """Map one normalized resource type and its immutable identity fields."""

    __tablename__ = "resources"

    resource_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    identity_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class OperationResourceEdgeORM(Base):
    """Map the proposition that one operation uses one resource in one role."""

    __tablename__ = "operation_resource_edges"
    __table_args__ = (
        CheckConstraint("_alpha >= 1", name="operation_resource_edge_alpha"),
        CheckConstraint("_beta >= 1", name="operation_resource_edge_beta"),
    )

    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        primary_key=True,
    )
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.resource_id"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(100), primary_key=True)
    _alpha: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    _beta: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ResourceInstanceORM(Base):
    """Map the latest recursively merged state of one typed resource instance."""

    __tablename__ = "resource_instances"

    resource_type: Mapped[str] = mapped_column(
        ForeignKey("resources.name"),
        primary_key=True,
    )
    resource_instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_state_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class AbstractTestCaseORM(CreatedAtMixin, Base):
    """Map one immutable Generator and Constraint snapshot used by a Batch."""

    __tablename__ = "abstract_test_cases"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "state_digest",
            name="abstract_test_case_operation_digest",
        ),
    )

    abstract_test_case_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        nullable=False,
        index=True,
    )
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    generators_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    constraints_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class BatchORM(Base):
    """Map one durable Batch identity to its complete structured summary."""

    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class ObservationORM(Base):
    """Map one completed HTTP exchange or request transport failure."""

    __tablename__ = "observations"
    __table_args__ = (
        Index(
            "ix_observations_operation_timestamp",
            "operation_id",
            "timestamp",
            "observation_id",
        ),
        CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="observation_http_status",
        ),
        CheckConstraint(
            "((outcome_kind = 'http' AND status_code IS NOT NULL "
            "AND response_headers IS NOT NULL AND response_body IS NOT NULL "
            "AND body_format IS NOT NULL AND transport_code IS NULL "
            "AND transport_message IS NULL) OR "
            "(outcome_kind = 'transport' AND status_code IS NULL "
            "AND reason_phrase IS NULL AND media_type IS NULL "
            "AND response_headers IS NULL AND response_body IS NULL "
            "AND body_format IS NULL AND transport_code IS NOT NULL "
            "AND transport_message IS NOT NULL))",
            name="observation_outcome_shape",
        ),
        CheckConstraint(
            "((batch_id IS NULL AND batch_case_index IS NULL) OR "
            "(batch_id IS NOT NULL AND batch_case_index IS NOT NULL "
            "AND batch_case_index >= 0))",
            name="observation_batch_shape",
        ),
        UniqueConstraint(
            "batch_id",
            "batch_case_index",
            name="observation_batch_case",
        ),
    )

    observation_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_phrase: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    response_headers: Mapped[dict[str, str] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    transport_code: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transport_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract_test_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("abstract_test_cases.abstract_test_case_id"),
        nullable=True,
    )
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("batches.batch_id"),
        nullable=True,
        index=True,
    )
    batch_case_index: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OperationInputSourceORM(Base):
    """Map one exact producer field selected for one consumer request input."""

    __tablename__ = "operation_input_sources"
    __table_args__ = (
        Index(
            "ix_operation_input_sources_producer",
            "producer_operation_id",
            "status_code",
            "media_type",
        ),
        CheckConstraint(
            "status_code >= 200 AND status_code <= 299",
            name="operation_input_source_success_status",
        ),
        CheckConstraint(
            "consume_type IN ('RESOURCE', 'VALUE_REUSE')",
            name="operation_input_source_consume_type",
        ),
        CheckConstraint("_alpha >= 1", name="operation_input_source_alpha"),
        CheckConstraint("_beta >= 1", name="operation_input_source_beta"),
    )

    consumer_operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        primary_key=True,
    )
    consumer_input_node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    producer_operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        primary_key=True,
    )
    status_code: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text, primary_key=True)
    selector: Mapped[str] = mapped_column(Text, primary_key=True)
    field_name: Mapped[str] = mapped_column(Text, primary_key=True)
    consume_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    _alpha: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    _beta: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
