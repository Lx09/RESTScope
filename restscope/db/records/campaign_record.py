from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CampaignRecord:
    id: str
    task_id: str
    schema_id: str
    target_env_id: str | None
    status: str
    campaign_type: str
    campaign_spec_json: dict[str, Any]
    validation_result_json: dict[str, Any] | None
    summary_json: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    artifact_bundle_uri: str | None
    created_at: datetime
