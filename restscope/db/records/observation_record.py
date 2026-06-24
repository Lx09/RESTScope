from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TestObservationRecord:
    id: str
    task_id: str
    campaign_id: str
    schema_id: str
    operation_id: str | None
    observation_type: str
    status: str
    severity: str
    confidence: Decimal
    dedupe_key: str
    check_id: str | None
    request_fingerprint: str | None
    response_fingerprint: str | None
    request_summary_json: dict[str, Any] | None
    response_summary_json: dict[str, Any] | None
    reproducer_artifact_id: str | None
    raw_artifact_id: str | None
    hypothesis: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
