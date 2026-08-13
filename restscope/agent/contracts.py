"""Define bounded contracts for generic Agent executions.

Subagents and focused internal callers receive one :class:`AgentTask`; the
taskless Main startup does not. Every model loop may finish only with
:class:`AgentCompletion` unless the Harness starts a registered System Agent
with a narrower result contract. Runtime failures are added internally, so
model-authored content cannot forge lifecycle state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentTask(BaseModel):
    """Give one Main Agent or Subagent a single bounded objective."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1, max_length=12_000)


class SystemAgentTask(BaseModel):
    """Give one Harness-started System Agent bounded decision evidence.

    ``allowed_result_aliases`` carries only the short, prompt-local identities
    that may appear in the structured result. The registered result contract
    decides how those aliases are represented and validated; callers cannot
    supply an arbitrary JSON Schema.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1, max_length=20_000)
    allowed_result_aliases: tuple[str, ...] = Field(default=(), max_length=100)
    allowed_result_paths: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("allowed_result_aliases")
    @classmethod
    def require_bounded_unique_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject aliases that cannot be shown safely in correction feedback."""
        if len(values) != len(set(values)):
            raise ValueError("System Agent result aliases must be unique")
        if any(not value.strip() or len(value) > 20 for value in values):
            raise ValueError("System Agent result aliases must be 1-20 characters")
        return values

    @field_validator("allowed_result_paths")
    @classmethod
    def require_bounded_unique_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep prompt-local OpenAPI paths unique and safe for feedback."""
        if len(values) != len(set(values)):
            raise ValueError("System Agent result paths must be unique")
        if any(not value.startswith("/") or len(value) > 1000 for value in values):
            raise ValueError("System Agent result paths must be absolute and bounded")
        return values


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
    def require_matching_payload(self) -> AgentResult:
        """Keep success and failure payloads mutually exclusive."""
        if self.status == "completed":
            if self.completion is None or self.error is not None:
                raise ValueError("Completed Agent result requires completion only")
        elif self.completion is not None or self.error is None:
            raise ValueError("Non-completed Agent result requires error only")
        return self


class SystemAgentResult(BaseModel):
    """Return one Harness-validated structured System Agent decision.

    The output is JSON data rather than a domain model so the generic Harness
    does not import Monitor-owned result types. Callers may trust a completed
    output because both the registered Pydantic model and its task-local rules
    have already accepted it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=160)
    profile_name: str = Field(min_length=1, max_length=120)
    status: AgentResultStatus
    output: dict[str, object] | None = None
    error: AgentError | None = None
    usage: AgentUsage = Field(default_factory=AgentUsage)

    @model_validator(mode="after")
    def require_matching_payload(self) -> SystemAgentResult:
        """Keep validated output and terminal failure mutually exclusive."""
        if self.status == "completed":
            if self.output is None or self.error is not None:
                raise ValueError("Completed System Agent result requires output only")
        elif self.output is not None or self.error is None:
            raise ValueError("Non-completed System Agent result requires error only")
        return self
