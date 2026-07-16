from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OperationEdgeRecord:
    id: str
    schema_id: str
    source_operation_id: str
    target_operation_id: str
    edge_type: str
    value: str | None
    confidence: float
    status: str
    reason: str
    created_at: datetime
