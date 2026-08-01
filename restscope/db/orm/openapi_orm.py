"""Store the normalized current OpenAPI document and its response-change audit.

The App writes the singleton document during initialization and updates it when
one observed HTTP response changes the in-memory response contract.  Event rows
retain only the affected Response before and after the change; raw HTTP bodies
and source-file locations never enter this persistence boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class OpenAPICurrentORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the one normalized OpenAPI document owned by the current App."""

    __tablename__ = "openapi_current"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="singleton_id_is_one"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class OpenAPIChangeEventORM(CreatedAtMixin, Base):
    """Map one append-only response-contract change caused by an observation."""

    __tablename__ = "openapi_change_events"
    __table_args__ = (
        Index("ix_openapi_change_events_operation_created", "operation_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_key: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    response_before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    response_after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
