from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class OperationIntelligenceRecord:
    operation_id: str
    schema_id: str
    test_state: str
    dynamic_risk_score: Decimal
    failure_density: Decimal
    flake_rate: Decimal
    last_tested_at: datetime | None
    total_campaigns: int
    total_cases_executed: int
    observation_count: int
    confirmed_issue_count: int
    server_error_count: int
    contract_violation_count: int
    semantic_violation_count: int
    flake_count: int
    learned_constraint_count: int
    high_confidence_constraint_count: int
    recommended_checks: list[str]
    regression_priority: Decimal
    summary_json: dict[str, Any]
    updated_at: datetime
