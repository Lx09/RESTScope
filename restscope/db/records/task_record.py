from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AgentTaskRecord:
    id: str
    schema_id: str
    target_env_id: str | None
    state: str
    goal_json: dict[str, Any]
    budget_json: dict[str, Any]
    cycle_index: int
    active_campaign_id: str | None
    selected_operation_ids: list[str]
    current_hypotheses: list[str]
    current_check_ids: list[str]
    context_snapshot_id: str | None
    latest_report_uri: str | None
    blockers_json: list[Any]
    last_error: str | None
    version: int
    created_at: datetime
    updated_at: datetime
