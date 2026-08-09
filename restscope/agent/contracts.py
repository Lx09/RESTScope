"""Define bounded contracts for task-scoped generic Agent executions.

Subagents and focused internal callers receive one :class:`AgentTask`; the
taskless Main startup does not. Every model loop may finish only with
:class:`AgentCompletion`. Runtime failures are added internally in an
:class:`AgentResult`, so model-authored content cannot forge lifecycle state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentTask(BaseModel):
    """Give one Main Agent or Subagent a single bounded objective."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1, max_length=12_000)


class AgentFinding(BaseModel):
    """Return one reusable conclusion and the opaque evidence that supports it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=4_000)
    confidence: Literal["low", "medium", "high"]
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("evidence_refs")
    @classmethod
    def require_unique_bounded_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous, blank, or oversized evidence identities."""
        if len(values) != len(set(values)):
            raise ValueError("Agent finding evidence references must be unique")
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Agent finding evidence references must be 1-200 characters")
        return values


class AgentCompletion(BaseModel):
    """The one deeply structured final answer allowed from any generic Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str = Field(min_length=1, max_length=16_000)
    findings: tuple[AgentFinding, ...] = Field(default=(), max_length=100)


class AgentUsage(BaseModel):
    """Expose bounded tree-accounting facts without model or Tool transcripts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    weighted_tokens: float = Field(default=0, ge=0)
    model_outputs: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    subagents_started: int = Field(default=0, ge=0)


class AgentError(BaseModel):
    """Return one stable, model-safe runtime failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=2_000)


AgentResultStatus = Literal[
    "completed",
    "failed",
    "cancelled",
    "rollout_budget_exceeded",
    "context_budget_exceeded",
    "context_compaction_failed",
]


class AgentResult(BaseModel):
    """Return one completed or terminal generic Agent task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=160)
    profile_name: str = Field(min_length=1, max_length=120)
    status: AgentResultStatus
    completion: AgentCompletion | None = None
    error: AgentError | None = None
    usage: AgentUsage = Field(default_factory=AgentUsage)

    @model_validator(mode="after")
    def require_matching_payload(self) -> "AgentResult":
        """Keep success and failure payloads mutually exclusive."""
        if self.status == "completed":
            if self.completion is None or self.error is not None:
                raise ValueError("Completed Agent result requires completion only")
        elif self.completion is not None or self.error is None:
            raise ValueError("Non-completed Agent result requires error only")
        return self
