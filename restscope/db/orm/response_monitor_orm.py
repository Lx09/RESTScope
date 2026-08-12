"""Map durable API response facts and the state derived from those facts.

The API Response Monitor writes successful JSON observations, then derives
operations, resources, current resource instances, and exact request-input
sources.  Request Generation may also record the immutable abstract
configuration that produced a Batch request.  These tables never store Agent
reasoning, scheduler state, or a restorable Generation State.
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin


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


class ObservationORM(Base):
    """Map one complete successful JSON request and response observation."""

    __tablename__ = "observations"
    __table_args__ = (
        Index(
            "ix_observations_operation_timestamp",
            "operation_id",
            "timestamp",
            "observation_id",
        ),
        CheckConstraint(
            "status_code >= 200 AND status_code <= 299",
            name="observation_success_status",
        ),
    )

    observation_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("operations.operation_id"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
    abstract_test_case_id: Mapped[str | None] = mapped_column(
        ForeignKey("abstract_test_cases.abstract_test_case_id"),
        nullable=True,
    )


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
