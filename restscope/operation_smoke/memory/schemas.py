"""Define structured Operation Smoke knowledge written and read during one App.

These models are the Memory Interface shared by Failure Dedup, Failure Solve,
and the SQLAlchemy Adapter. Write models contain only bounded summaries and
necessary input values; full Batch evidence, response bodies, and model
transcripts never cross this seam. Read models deliberately assemble the
relationships that an Agent needs so callers do not understand database tables
or joins.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _MemoryModel(BaseModel):
    """Reject accidental persistence fields at the Memory Interface."""

    model_config = ConfigDict(extra="forbid")


class FailureObservationWrite(_MemoryModel):
    """Describe one concrete failed case without persisting its raw response."""

    observation_key: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    necessary_values: dict[str, Any] = Field(default_factory=dict)


class FailureWrite(_MemoryModel):
    """Write one new current-round Failure and its representative Observation."""

    summary: str = Field(min_length=1)
    observations: list[FailureObservationWrite] = Field(default_factory=list)
    suspected_parameters: list[str] | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> "FailureWrite":
        """Require the single representative evidence approved for each Failure."""
        if len(self.observations) != 1:
            raise ValueError("each Failure requires exactly one Observation")
        return self


class FailureBatchWrite(_MemoryModel):
    """Record validated current-round Failures after Dedup succeeds."""

    operation_key: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1)
    failures: list[FailureWrite]


class RecordedFailure(_MemoryModel):
    """Return the stable Failure identity assigned to one classification."""

    failure_id: str
    summary: str


class RecordedFailures(_MemoryModel):
    """Return stable identities in the same order as the Dedup Failures."""

    failures: list[RecordedFailure]


class InvestigationParameterWrite(_MemoryModel):
    """Attribute one Investigation conclusion to an exact operation input."""

    input_node_id: str = Field(min_length=1)
    cause_summary: str = Field(min_length=1)


class AppliedPatchWrite(_MemoryModel):
    """Persist the exact accepted Generator/Constraint change and its samples."""

    generator_revision: int = Field(ge=1)
    patch: dict[str, Any]
    before_generators: dict[str, Any] = Field(default_factory=dict)
    after_generators: dict[str, Any] = Field(default_factory=dict)
    samples: list[dict[str, Any]] = Field(default_factory=list)


class InvestigationWrite(_MemoryModel):
    """Append one terminal Solve conclusion to a stable Failure."""

    operation_key: str = Field(min_length=1)
    failure_id: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    trigger_conditions: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    solution: str = Field(min_length=1)
    evidence_source: Literal["batch", "memory", "http_probe", "mixed"]
    parameters: list[InvestigationParameterWrite] = Field(default_factory=list)
    conflict_reason: str | None = Field(default=None, min_length=1)
    applied_patch: AppliedPatchWrite | None = None

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> "InvestigationWrite":
        """Keep durable Patch and conflict facts consistent with the outcome."""
        if self.outcome == "applied_patch" and self.applied_patch is None:
            raise ValueError("applied_patch outcome requires applied_patch")
        if self.outcome != "applied_patch" and self.applied_patch is not None:
            raise ValueError("only applied_patch outcomes persist a Patch")
        if self.outcome == "conflict" and not self.conflict_reason:
            raise ValueError("conflict outcome requires conflict_reason")
        return self


class AppliedPatchMemory(_MemoryModel):
    """Read projection of one Patch that actually changed Generator state."""

    generator_revision: int
    patch: dict[str, Any]
    before_generators: dict[str, Any]
    after_generators: dict[str, Any]
    samples: list[dict[str, Any]]


class InvestigationMemory(_MemoryModel):
    """Read projection of one chronological Solve Investigation."""

    investigation_id: str
    round_number: int
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    trigger_conditions: str
    root_cause: str
    solution: str
    evidence_source: Literal["batch", "memory", "http_probe", "mixed"]
    conflict_reason: str | None = None
    parameters: list[InvestigationParameterWrite] = Field(default_factory=list)
    applied_patch: AppliedPatchMemory | None = None


class FailureObservationMemory(_MemoryModel):
    """Read projection of one bounded Failure Observation."""

    batch_run_id: str
    round_number: int
    observation_key: str
    trigger: str
    response_summary: dict[str, Any]
    necessary_values: dict[str, Any]
class FailureHistory(_MemoryModel):
    """Complete structured history used by Solve."""

    failure_id: str
    summary: str
    observations: list[FailureObservationMemory] = Field(default_factory=list)
    investigations: list[InvestigationMemory] = Field(default_factory=list)


class ParameterHistory(_MemoryModel):
    """Solve-facing history for one exact operation input."""

    input_node_id: str
    failures: list[FailureHistory] = Field(default_factory=list)
