"""ORM mapping for context snapshots."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType


class ContextSnapshotORM(CreatedAtMixin, Base):
    __tablename__ = "context_snapshots"
    __table_args__ = (
        Index("idx_context_snapshots_task", "task_id", "cycle_index"),
        Index("idx_context_snapshots_role", "schema_id", "role"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), nullable=False)
    schema_id: Mapped[str] = mapped_column(ForeignKey("schemas.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    cycle_index: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String, nullable=False)
    source_refs_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    total_estimated_tokens: Mapped[int | None] = mapped_column(Integer)
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
