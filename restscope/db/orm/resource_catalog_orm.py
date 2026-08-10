"""Map the durable Resource Identifier evidence learned from API responses.

The tables keep canonical resource vocabulary, typed identifier values, and
only the latest operation/error usage facts.  Method and path remain owned by
the current OpenAPI document instead of being copied into these rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class ResourceORM(CreatedAtMixin, Base):
    """Map one canonical resource identity."""

    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class ResourceAliasORM(CreatedAtMixin, Base):
    """Map one normalized alias directly to its canonical resource."""

    __tablename__ = "resource_aliases"

    normalized_alias: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)


class OperationResourceRuleORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the latest resource classification for one response group."""

    __tablename__ = "operation_resource_rules"
    __table_args__ = (
        UniqueConstraint("operation_key", "group_path", name="operation_group"),
        CheckConstraint(
            "(has_resource AND resource_id IS NOT NULL "
            "AND id_field_name IS NOT NULL AND id_selector IS NOT NULL) OR "
            "((NOT has_resource) AND resource_id IS NULL "
            "AND id_field_name IS NULL AND id_selector IS NULL)",
            name="resource_rule_shape",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("resources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    group_path: Mapped[str] = mapped_column(Text, nullable=False)
    has_resource: Mapped[bool] = mapped_column(Boolean, nullable=False)
    id_field_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_mode: Mapped[str] = mapped_column(String, nullable=False)
    classification_source: Mapped[str] = mapped_column(String, nullable=False)


class ResourceIdentifierORM(Base):
    """Map one typed identifier value observed for a canonical resource."""

    __tablename__ = "resource_identifiers"
    __table_args__ = (
        UniqueConstraint("resource_id", "value_type", "value_text", name="resource_typed_value"),
        CheckConstraint(
            "value_type IN ('string', 'integer', 'number', 'boolean')",
            name="resource_identifier_scalar_type",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value_type: Mapped[str] = mapped_column(String, nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceOperationUsageORM(Base):
    """Map the latest observation of an identifier in one operation rule."""

    __tablename__ = "resource_operation_usages"

    identifier_id: Mapped[str] = mapped_column(
        ForeignKey("resource_identifiers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    operation_rule_id: Mapped[str] = mapped_column(
        ForeignKey("operation_resource_rules.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    latest_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceMonitorErrorORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map only the latest monitor error for one operation response group."""

    __tablename__ = "resource_monitor_errors"

    operation_key: Mapped[str] = mapped_column(Text, primary_key=True)
    group_path: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("resources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    issues: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
