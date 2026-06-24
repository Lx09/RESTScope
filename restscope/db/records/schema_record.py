from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SchemaRecord:
    id: str
    name: str
    version: str | None
    spec_hash: str
    raw_spec_uri: str
    normalized_spec_uri: str | None
    openapi_version: str | None
    operation_count: int
    created_at: datetime
