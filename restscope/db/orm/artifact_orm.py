"""ORM mapping for artifacts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, CreatedAtMixin
from ..types import JsonType


class ArtifactORM(CreatedAtMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("idx_artifacts_task", "task_id"),
        Index("idx_artifacts_campaign", "campaign_id"),
        Index("idx_artifacts_observation", "observation_id"),
        Index("idx_artifacts_type", "artifact_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String)
    campaign_id: Mapped[str | None] = mapped_column(String)
    observation_id: Mapped[str | None] = mapped_column(String)
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
