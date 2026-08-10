"""Map bounded response observations and registered typed value pools.

Natural composite keys replace UUID-only identities.  Parent deletion cascades
only to its dependent selectors, values, or scalar rows; ordinary runtime
retention deletes old values and observations deterministically.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class ResponseValuePoolORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the one response-value pool serving a consumer input."""

    __tablename__ = "response_value_pools"
    __table_args__ = (
        UniqueConstraint("consumer_operation_key", "consumer_input_node_id", name="consumer_input"),
    )

    value_name: Mapped[str] = mapped_column(Text, primary_key=True)
    consumer_operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    consumer_input_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_name: Mapped[str] = mapped_column(Text, nullable=False)
    expected_type: Mapped[str | None] = mapped_column(String, nullable=True)


class ResponseValuePoolSourceORM(CreatedAtMixin, Base):
    """Map one explicit producer selector feeding a named value pool."""

    __tablename__ = "response_value_pool_sources"
    __table_args__ = (
        Index("ix_response_value_pool_sources_producer", "producer_operation_key"),
    )

    value_name: Mapped[str] = mapped_column(
        ForeignKey("response_value_pools.value_name", ondelete="CASCADE"),
        primary_key=True,
    )
    producer_operation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    status_code: Mapped[str] = mapped_column(String, primary_key=True)
    media_type: Mapped[str] = mapped_column(Text, primary_key=True)
    selector: Mapped[str] = mapped_column(Text, primary_key=True)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)


class ResponseValuePoolValueORM(Base):
    """Map one recently active typed value in a named response pool."""

    __tablename__ = "response_value_pool_values"
    __table_args__ = (
        Index("ix_response_value_pool_values_pool_last_seen", "value_name", "last_seen_at"),
        CheckConstraint(
            "value_type IN ('string', 'integer', 'number', 'boolean')",
            name="response_pool_scalar_type",
        ),
    )

    value_name: Mapped[str] = mapped_column(
        ForeignKey("response_value_pools.value_name", ondelete="CASCADE"),
        primary_key=True,
    )
    value_type: Mapped[str] = mapped_column(String, primary_key=True)
    value_text: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResponseObservationORM(Base):
    """Map one bounded successful JSON response observation."""

    __tablename__ = "response_observations"
    __table_args__ = (
        Index("ix_response_observations_operation_time", "operation_key", "observed_at"),
        CheckConstraint(
            "status_code >= 100 AND status_code <= 599",
            name="response_observation_http_status",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResponseObservationScalarORM(Base):
    """Map one distinct selector and typed value from an observation."""

    __tablename__ = "response_observation_scalars"
    __table_args__ = (
        CheckConstraint(
            "value_type IN ('string', 'integer', 'number', 'boolean')",
            name="response_observation_scalar_type",
        ),
        CheckConstraint(
            "position >= 0",
            name="response_observation_scalar_position",
        ),
    )

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("response_observations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    selector: Mapped[str] = mapped_column(Text, primary_key=True)
    value_type: Mapped[str] = mapped_column(String, primary_key=True)
    value_text: Mapped[str] = mapped_column(Text, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
