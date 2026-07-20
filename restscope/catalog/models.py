"""Domain models for stored OpenAPI sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, model_validator


class SchemaSourceInput(BaseModel):
    """Exactly one durable source for an OpenAPI document."""

    file_path: Path | None = None
    raw_content: str | None = None

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> "SchemaSourceInput":
        if (self.file_path is None) == (self.raw_content is None):
            raise ValueError("Exactly one of file_path or raw_content is required")
        if self.raw_content is not None and not self.raw_content.strip():
            raise ValueError("raw_content must not be blank")
        return self


@dataclass(frozen=True)
class SchemaRecord:
    """Stored schema source without persistence-framework types."""

    id: str
    file_path: str | None
    raw_content: str | None
    created_at: datetime
    updated_at: datetime
