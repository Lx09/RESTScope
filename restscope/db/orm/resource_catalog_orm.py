"""Map the durable Resource Identifier evidence learned from API responses.

The tables keep canonical resource vocabulary, ordered Identifier Definitions,
complete typed Identifier Records, and only the latest operation/error usage
facts. Method and operation path remain owned by the current OpenAPI document;
only a rule's selected identifier-evidence path is stored.
"""

from __future__ import annotations

from datetime import datetime
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
            "(has_resource AND resource_id IS NOT NULL AND identifier_definition_id IS NOT NULL) OR "
            "((NOT has_resource) AND resource_id IS NULL AND identifier_definition_id IS NULL)",
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
    identifier_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("resource_identifier_definitions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    identifier_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    identifier_fields: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    access_mode: Mapped[str] = mapped_column(String, nullable=False)
    classification_source: Mapped[str] = mapped_column(String, nullable=False)


class ResourceIdentifierDefinitionORM(CreatedAtMixin, Base):
    """Map one named ordered Identifier Definition for a resource."""

    __tablename__ = "resource_identifier_definitions"
    __table_args__ = (
        UniqueConstraint("resource_id", "name", name="resource_identifier_definition_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    component_names: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class ResourceIdentifierORM(Base):
    """Map one complete ordered Identifier Record."""

    __tablename__ = "resource_identifiers"
    __table_args__ = (
        UniqueConstraint("definition_id", "value_digest", name="resource_identifier_record"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    definition_id: Mapped[str] = mapped_column(
        ForeignKey("resource_identifier_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    values: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    value_digest: Mapped[str] = mapped_column(String(64), nullable=False)
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
    issues: Mapped[list[object]] = mapped_column(JSON, nullable=False)
