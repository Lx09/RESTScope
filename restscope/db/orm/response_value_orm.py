"""ORM mappings for persistent response-value monitors and value pools."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class ResponseValueMonitorORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """
    Map persisted response value monitor rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "response_value_monitors"
    __table_args__ = (
        UniqueConstraint(
            "consumer_operation_key",
            "consumer_input_node_id",
            name="uq_response_value_monitor_consumer_input",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    value_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    consumer_operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    consumer_input_node_id: Mapped[str] = mapped_column(Text, nullable=False)
    parameter_name: Mapped[str] = mapped_column(Text, nullable=False)
    expected_type: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ResponseValueSourceORM(CreatedAtMixin, Base):
    """
    Map persisted response value source rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "response_value_sources"
    __table_args__ = (
        UniqueConstraint(
            "monitor_id",
            "producer_operation_key",
            "status_code",
            "media_type",
            "selector",
            name="uq_response_value_source",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("response_value_monitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    producer_operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    selector: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)


class ResponseValueORM(Base):
    """
    Map persisted response value rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "response_values"
    __table_args__ = (
        UniqueConstraint(
            "monitor_id",
            "value_type",
            "value_text",
            name="uq_response_value_typed_value",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    monitor_id: Mapped[str] = mapped_column(
        ForeignKey("response_value_monitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResponseObservationORM(Base):
    """
    Map persisted response observation rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "response_observations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ResponseObservationScalarORM(Base):
    """
    Map persisted response observation scalar rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "response_observation_scalars"
    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "selector",
            "value_type",
            "value_text",
            name="uq_response_observation_scalar_value",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("response_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    selector: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
