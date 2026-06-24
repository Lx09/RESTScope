from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class OperationRecord:
    id: str
    schema_id: str
    operation_id: str | None
    method: str
    path: str
    tags: list[str]
    summary: str | None
    resource: str | None
    mutability: str | None
    security: dict[str, Any] | None
    request_schema_refs: list[str]
    response_schema_refs: list[str]
    card_json: dict[str, Any]
    static_risk_score: Decimal
    created_at: datetime
