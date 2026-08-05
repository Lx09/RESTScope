"""Short-lived contracts for independent semantic Patch review.

The Coordinator supplies normalized facts about one compiled candidate. The
Review Agent returns concrete semantic issues only; none of these DTOs are
persisted or exposed through Failure Resolution's candidate Interface.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    """Reject unknown fields in immutable Reviewer handoffs."""

    model_config = ConfigDict(frozen=True, extra="forbid")


ReviewIssue = Annotated[str, Field(min_length=1, max_length=800)]


class ParameterPatchReviewCandidate(_Model):
    """Contain only normalized final facts needed to review one candidate."""

    requirement: dict[str, Any]
    affected_inputs: list[str]
    before_generators: dict[str, Any]
    after_generators: dict[str, Any]
    proposal: dict[str, Any]
    reference_provenance: list[dict[str, Any]] = Field(default_factory=list)
    active_constraints: list[dict[str, Any]] = Field(default_factory=list)
    candidate_constraints: list[dict[str, Any]] = Field(default_factory=list)
    samples: list[dict[str, Any]]


class ParameterPatchReviewSubmission(_Model):
    """Capture concrete semantic mismatches reported by the Reviewer."""

    issues: list[ReviewIssue] = Field(max_length=20)


class ParameterPatchReviewResult(_Model):
    """Return a normalized semantic verdict and its model-output cost."""

    status: Literal["reviewed"] = "reviewed"
    accepted: bool
    issues: list[ReviewIssue] = Field(max_length=20)
    outputs_used: int = Field(ge=1, le=1_000)
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)
