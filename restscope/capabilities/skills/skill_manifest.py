"""Skill metadata used for prompt guidance and future capability selection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SkillManifest(BaseModel):
    """Metadata for a reusable skill bundle."""

    name: str
    description: str
    version: str | None = None
    entrypoint: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    instruction_artifact_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
