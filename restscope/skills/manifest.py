"""Skill metadata selected explicitly by an independent Agent Profile."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_REFERENCE_PATH_PATTERN = r"^references/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"


class SkillReference(BaseModel):
    """Hold one validated, lazily readable Markdown method document.

    Args:
        path: Skill-relative name under the one-level ``references`` directory.
        content: Complete UTF-8 Markdown loaded and bounded during App startup.

    The runtime file Tool reads this immutable value from memory. It never
    resolves ``path`` against the live filesystem after an Agent starts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=15, max_length=1_000, pattern=_REFERENCE_PATH_PATTERN)
    content: str = Field(min_length=1, max_length=24_000)


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
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("required_tools", "required_context_sources")
    @classmethod
    def require_unique_requirements(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate access requirements before Profile validation."""
        if len(values) != len(set(values)):
            raise ValueError("Skill access requirements must be unique")
        return values


class SkillDefinition(BaseModel):
    """Pair immutable Skill metadata with core instructions and References.

    ``instructions`` contains only the ``SKILL.md`` body. References are
    separately bounded Markdown files and enter an Agent conversation only
    through an explicitly granted ``file.read`` call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest: SkillManifest
    instructions: str = Field(min_length=1, max_length=24_000)
    references: tuple[SkillReference, ...] = Field(default=(), max_length=100)

    @field_validator("references")
    @classmethod
    def require_unique_reference_paths(
        cls,
        references: tuple[SkillReference, ...],
    ) -> tuple[SkillReference, ...]:
        """Reject ambiguous in-memory paths before a file reader is bound."""
        paths = [reference.path for reference in references]
        if len(paths) != len(set(paths)):
            raise ValueError("Skill Reference paths must be unique")
        return references

    @property
    def name(self) -> str:
        """Return the stable name used by an Agent Profile."""
        return self.manifest.name

    def reference(self, path: str) -> SkillReference:
        """Return one registered Reference without consulting the filesystem.

        Args:
            path: Exact one-level path recorded during standard Skill loading.

        Raises:
            KeyError: When this Skill did not directly link the requested file.
        """
        for reference in self.references:
            if reference.path == path:
                return reference
        raise KeyError(f"Unknown Skill Reference: {self.name}/{path}")
