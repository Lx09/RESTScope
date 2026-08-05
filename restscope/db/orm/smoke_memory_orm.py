"""Map stable Failure knowledge and append-only terminal Attempts.

Detailed Test Cases remain in the run-local catalog.  Persistence keeps only a
stable Failure identity, occurrence metadata, each terminal Resolution
conclusion, and optional per-input causal attribution needed by later sessions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin


class SmokeFailureORM(Base):
    """Map one stable semantic Failure reused across matching Batch rounds."""

    __tablename__ = "smoke_failures"
    __table_args__ = (
        Index("ix_smoke_failures_operation", "operation_key"),
        CheckConstraint("occurrence_count >= 1", name="positive_occurrence_count"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    failure_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_messages: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    suspected_input_node_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SmokeSolveAttemptORM(CreatedAtMixin, Base):
    """Map one immutable terminal conclusion produced by Failure Resolution."""

    __tablename__ = "smoke_solve_attempts"
    __table_args__ = (
        Index("ix_smoke_solve_attempts_failure_created", "failure_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    failure_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_failures.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)


class SmokeSolveAttemptParameterORM(Base):
    """Map one validated input attribution attached to a terminal Attempt."""

    __tablename__ = "smoke_solve_attempt_parameters"

    solve_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_solve_attempts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    input_node_id: Mapped[str] = mapped_column(
        ForeignKey("input_generator_configs.input_node_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    cause_summary: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
