"""Describe one independent Agent's explicitly granted model-facing access.

Profiles contain names rather than live implementations. The deterministic
Harness resolves those names against the Tool and Skill Catalogs and binds
only the context sources available for the current run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentProfile(BaseModel):
    """Declare the model and named capabilities available to one Agent.

    Args:
        name: Stable configuration name used by the Harness and observability.
        description: Optional plain-language purpose shown to a direct parent.
        instructions: Optional stable guidance shown to this Agent itself.
        model_config_name: Name of the exact provider/model configuration.
        tool_names: Global Tool names this Agent may invoke.
        skill_names: Reusable instruction bundles this Agent may receive.
        context_sources: Bounded evidence sources the Harness may render.
        subagent_profile_names: Exact child Profiles this Agent may start.

    A Profile grants no access by itself. Harness construction must resolve and
    validate every name before an Agent session starts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_.-]*$")
    description: str | None = Field(default=None, min_length=1, max_length=2_000)
    instructions: str | None = Field(default=None, min_length=1, max_length=12_000)
    model_config_name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    tool_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    context_sources: tuple[str, ...] = ()
    subagent_profile_names: tuple[str, ...] = ()

    @field_validator("instructions")
    @classmethod
    def require_nonblank_instructions(cls, value: str | None) -> str | None:
        """Reject whitespace-only guidance without rewriting trusted text."""
        if value is not None and not value.strip():
            raise ValueError("Agent Profile instructions must not be blank")
        return value

    @field_validator(
        "tool_names",
        "skill_names",
        "context_sources",
        "subagent_profile_names",
    )
    @classmethod
    def require_unique_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicated grants so Profile review has one clear meaning."""
        if len(values) != len(set(values)):
            raise ValueError("Agent Profile access names must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("Agent Profile access names must not be blank")
        return values
