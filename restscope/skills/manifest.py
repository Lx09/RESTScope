"""Skill metadata selected explicitly by an independent Agent Profile."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillManifest(BaseModel):
    """Describe reusable instructions and the bounded access they need.

    A Skill neither executes code nor owns runtime state. The Harness selects
    it only when an Agent Profile names it and verifies every required Tool and
    context source is granted to the same Profile.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2_000)
    version: str | None = None
    required_tools: tuple[str, ...] = ()
    required_context_sources: tuple[str, ...] = ()
    risk_level: Literal["low", "medium", "high"] = "low"
    instruction_artifact_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("required_tools", "required_context_sources")
    @classmethod
    def require_unique_requirements(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate access requirements before Profile validation."""
        if len(values) != len(set(values)):
            raise ValueError("Skill access requirements must be unique")
        return values


class SkillDefinition(BaseModel):
    """Pair immutable Skill metadata with instructions loaded by the App.

    The Harness receives instruction text directly from its composition root;
    it never follows ``instruction_artifact_uri`` while an Agent is starting.
    This keeps filesystem access outside the model-session authorization path.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: SkillManifest
    instructions: str = Field(min_length=1, max_length=24_000)

    @property
    def name(self) -> str:
        """Return the stable name used by an Agent Profile."""
        return self.manifest.name
