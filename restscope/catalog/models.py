"""Database-independent contracts for OpenAPI audit persistence.

The current document is a normalized OpenAPI mapping built from the App's IR.
Change records contain only the affected Response before and after a runtime
observation so callers can inspect evolution without storing raw HTTP data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _CatalogModel(BaseModel):
    """Reject unknown persistence fields at the catalog boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OpenAPIChangeEventWrite(_CatalogModel):
    """Describe one validated response change before database insertion."""

    operation_key: str = Field(min_length=1)
    status_code: int = Field(ge=100, le=599)
    media_type: str | None = None
    changes: list[str] = Field(min_length=1)
    response_before: dict[str, Any] | None = None
    response_after: dict[str, Any]


class OpenAPIChangeEventRecord(OpenAPIChangeEventWrite):
    """Return one persisted change event through the read-only audit API."""

    id: str
    created_at: datetime
