"""ORM mappings for operation input generator configuration."""

from __future__ import annotations

from typing import Any

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from restscope.testing.models import GeneratorRevisionLifecycle

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class GeneratorCatalogStateORM(CreatedAtMixin, Base):
    """
    Map persisted generator catalog state rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "generator_catalog_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class OperationGeneratorConfigORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """
    Map persisted operation generator config rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "operation_generator_configs"

    operation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    disabled_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    active_media_type: Mapped[str | None] = mapped_column(String)


class InputGeneratorConfigORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """
    Map persisted input generator config rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "input_generator_configs"

    input_node_id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(
        ForeignKey("operation_generator_configs.operation_key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    inclusion_probability: Mapped[float] = mapped_column(Float, nullable=False)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class GeneratorConfigRevisionORM(CreatedAtMixin, Base):
    """
    Map persisted generator config revision rows to a database table.

    Repository classes use this mapping; runtime and Agent code should not manipulate
    these rows directly.
    """
    __tablename__ = "generator_config_revisions"

    operation_key: Mapped[str] = mapped_column(
        ForeignKey("operation_generator_configs.operation_key", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_revision: Mapped[int | None] = mapped_column(Integer)
    lifecycle: Mapped[GeneratorRevisionLifecycle] = mapped_column(
        String(20),
        nullable=False,
    )
    rollback_of_revision: Mapped[int | None] = mapped_column(Integer)
    restored_from_revision: Mapped[int | None] = mapped_column(Integer)
    hypothesis: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evaluation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
