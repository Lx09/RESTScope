"""ORM mappings for the single-App resource catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class ResourceORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class ResourceAliasORM(CreatedAtMixin, Base):
    __tablename__ = "resource_aliases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class OperationResourceRuleORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "operation_resource_rules"
    __table_args__ = (
        UniqueConstraint("operation_key", "group_path", name="uq_operation_resource_rule"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    group_path: Mapped[str] = mapped_column(Text, nullable=False)
    has_resource: Mapped[bool] = mapped_column(Boolean, nullable=False)
    resource_aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    id_field_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_mode: Mapped[str] = mapped_column(String, nullable=False)
    classification_source: Mapped[str] = mapped_column(String, nullable=False)
    id_observed: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ResourceIdentifierORM(CreatedAtMixin, Base):
    __tablename__ = "resource_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "resource_id",
            "value_type",
            "value_text",
            name="uq_resource_identifier_value",
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
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceOperationUsageORM(Base):
    __tablename__ = "resource_operation_usages"
    __table_args__ = (
        UniqueConstraint(
            "identifier_id",
            "operation_rule_id",
            name="uq_resource_operation_usage",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    identifier_id: Mapped[str] = mapped_column(
        ForeignKey("resource_identifiers.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_rule_id: Mapped[str] = mapped_column(
        ForeignKey("operation_resource_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_mode: Mapped[str] = mapped_column(String, nullable=False)
    latest_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceMonitorErrorORM(CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "resource_monitor_errors"
    __table_args__ = (
        UniqueConstraint("operation_key", "group_path", name="uq_resource_monitor_error"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str | None] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    group_path: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    issues: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
