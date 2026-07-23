"""ORM mappings for operation input generator configuration."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class GeneratorCatalogStateORM(CreatedAtMixin, Base):
    __tablename__ = "generator_catalog_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class OperationGeneratorConfigORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "operation_generator_configs"

    operation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    disabled_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    active_media_type: Mapped[str | None] = mapped_column(String)


class InputGeneratorConfigORM(CreatedAtMixin, UpdatedAtMixin, Base):
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
