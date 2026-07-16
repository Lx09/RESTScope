from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


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
    normalized_spec_json: dict[str, Any] | None
    parse_diagnostics_json: dict[str, Any]
    catalog_status: str
    catalog_slot: str | None
    parser_version: str | None
    initialized_at: datetime | None
    created_at: datetime
