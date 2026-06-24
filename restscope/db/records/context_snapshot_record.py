from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ContextSnapshotRecord:
    id: str
    task_id: str
    schema_id: str
    role: str
    cycle_index: int
    artifact_uri: str
    source_refs_json: dict[str, Any] | None
    total_estimated_tokens: int | None
    prompt_version: str
    model_name: str
    created_at: datetime
