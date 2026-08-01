"""Map current input Generators, executable Constraints, and accepted changes.

Operation request snapshots stay in memory because the App never reopens an
existing database.  These tables retain only mutable current values plus the
small before/after audit produced when Solve accepts a complete Patch.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class InputGeneratorConfigORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the current Generator strategy for one stable OpenAPI input node."""

    __tablename__ = "input_generator_configs"
    __table_args__ = (
        CheckConstraint(
            "inclusion_probability >= 0 AND inclusion_probability <= 1",
            name="inclusion_probability_range",
        ),
        Index("ix_input_generator_configs_operation", "operation_key", "position"),
    )

    input_node_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    inclusion_probability: Mapped[float] = mapped_column(Float, nullable=False)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class OperationConstraintORM(CreatedAtMixin, Base):
    """Map one normalized executable Constraint and its derived input owner."""

    __tablename__ = "operation_constraints"
    __table_args__ = (
        Index("ix_operation_constraints_operation", "operation_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    owner_input_node_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    expression: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class GeneratorChangeEventORM(CreatedAtMixin, Base):
    """Map the deterministic before/after diff for one accepted Solve Patch."""

    __tablename__ = "generator_change_events"
    __table_args__ = (
        Index("ix_generator_change_events_operation_created", "operation_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    solve_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_solve_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    generator_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    constraint_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
