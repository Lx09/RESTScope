"""Map structured Operation Smoke memory to normalized relational tables.

The mappings deliberately keep Failure, Observation, Investigation, Parameter,
and Applied Patch as separate concepts.  This makes both required query
directions efficient: Planner starts from an operation's Failures, while Solve
starts from an exact operation Parameter.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin


class SmokeFailureORM(CreatedAtMixin, Base):
    """Store one stable Planner Failure classification for one operation."""

    __tablename__ = "smoke_failures"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)


class SmokeObservationORM(CreatedAtMixin, Base):
    """Store one bounded failed-case summary once, even if Failures share it."""

    __tablename__ = "smoke_failure_observations"
    __table_args__ = (
        UniqueConstraint(
            "operation_key",
            "batch_run_id",
            "observation_key",
            name="uq_smoke_observation_batch_case",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    batch_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_key: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    response_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    necessary_values: Mapped[dict] = mapped_column(JSON, nullable=False)


class SmokeFailureObservationORM(Base):
    """Link one Observation to every semantic Failure it supports."""

    __tablename__ = "smoke_failure_observation_links"
    __table_args__ = (
        UniqueConstraint(
            "failure_id",
            "observation_id",
            name="uq_smoke_failure_observation_link",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    failure_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_failures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_failure_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    disposition: Mapped[str] = mapped_column(String, nullable=False)
    disposition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SmokeParameterORM(CreatedAtMixin, Base):
    """Store the exact operation-local identity of one request input."""

    __tablename__ = "smoke_parameters"
    __table_args__ = (
        UniqueConstraint(
            "operation_key",
            "input_node_id",
            name="uq_smoke_parameter_operation_input",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    input_node_id: Mapped[str] = mapped_column(Text, nullable=False)


class SmokeInvestigationORM(CreatedAtMixin, Base):
    """Store one append-only terminal Failure Solve conclusion."""

    __tablename__ = "smoke_investigations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    failure_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_failures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    trigger_conditions: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    solution: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_source: Mapped[str] = mapped_column(String, nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class SmokeInvestigationParameterORM(Base):
    """Link an Investigation to every Parameter involved in its conclusion."""

    __tablename__ = "smoke_investigation_parameter_links"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id",
            "parameter_id",
            name="uq_smoke_investigation_parameter_link",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parameter_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_parameters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cause_summary: Mapped[str] = mapped_column(Text, nullable=False)


class SmokeAppliedPatchORM(CreatedAtMixin, Base):
    """Store one Patch only after it becomes an accepted Generator revision."""

    __tablename__ = "smoke_applied_patches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("smoke_investigations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    generator_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    patch: Mapped[dict] = mapped_column(JSON, nullable=False)
    before_generators: Mapped[dict] = mapped_column(JSON, nullable=False)
    after_generators: Mapped[dict] = mapped_column(JSON, nullable=False)
    samples: Mapped[list] = mapped_column(JSON, nullable=False)
