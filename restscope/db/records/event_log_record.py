from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EventLogRecord:
    id: int
    task_id: str | None
    campaign_id: str | None
    event_type: str
    actor: str
    from_state: str | None
    to_state: str | None
    payload_json: dict[str, Any]
    created_at: datetime
