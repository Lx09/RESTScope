from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    task_id: str | None
    campaign_id: str | None
    observation_id: str | None
    artifact_type: str
    artifact_uri: str
    content_hash: str | None
    size_bytes: int | None
    metadata_json: dict[str, Any] | None
    created_at: datetime
