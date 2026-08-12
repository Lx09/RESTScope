"""Store the normalized current OpenAPI document and its response-change audit.

The App writes the singleton document during initialization and updates it when
one observed HTTP response changes the in-memory response contract.  Event rows
retain only the affected Response before and after the change; raw HTTP bodies
and source-file locations never enter this persistence boundary.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin, UpdatedAtMixin


class OpenAPICurrentORM(CreatedAtMixin, UpdatedAtMixin, Base):
    """Map the one normalized OpenAPI document owned by the current App."""

    __tablename__ = "openapi_current"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="singleton_id_is_one"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class OpenAPIChangeEventORM(CreatedAtMixin, Base):
    """Map one append-only response-contract change caused by an observation."""

    __tablename__ = "openapi_change_events"
    __table_args__ = (
        Index("ix_openapi_change_events_operation_created", "operation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # OpenAPI Audit remains independently usable during transaction tests and
    # exports. The operation text shares the normalized spelling but does not
    # require Response Monitor metadata to have been initialized first.
    operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    changes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    response_before: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    response_after: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
