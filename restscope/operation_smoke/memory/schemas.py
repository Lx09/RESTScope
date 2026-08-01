"""Define stable Failure and terminal Solve Attempt persistence contracts.

Failure Dedup writes normalized semantic groups without storing Test Cases.
Failure Solve appends terminal conclusions and optional input attribution.
Accepted Generator/Constraint diffs are exposed with their Solve Attempt, while
Patch samples, response bodies, and Agent transcripts never cross this seam.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _MemoryModel(BaseModel):
    """Reject accidental persistence fields at the workflow boundary."""

    model_config = ConfigDict(extra="forbid")


class FailureWrite(_MemoryModel):
    """Describe one stable Failure occurrence produced by current-Batch Dedup."""

    summary: str = Field(min_length=1)
    messages: list[str] = Field(min_length=1, max_length=100)
    suspected_input_node_ids: list[str] | None = None
    last_status_code: int | None = Field(default=None, ge=100, le=599)

    @model_validator(mode="after")
    def validate_sets(self) -> "FailureWrite":
        """Require unique messages and suspected input identities."""

        if len(self.messages) != len(set(self.messages)):
            raise ValueError("Failure messages must be unique")
        if (
            self.suspected_input_node_ids is not None
            and len(self.suspected_input_node_ids)
            != len(set(self.suspected_input_node_ids))
        ):
            raise ValueError("suspected input node IDs must be unique")
        return self


class FailureBatchWrite(_MemoryModel):
    """Record every validated Failure group from one completed Dedup step."""

    operation_key: str = Field(min_length=1)
    failures: list[FailureWrite] = Field(min_length=1)


class RecordedFailure(_MemoryModel):
    """Return the durable identity assigned or reused for one Failure."""

    failure_id: str
    summary: str


class RecordedFailures(_MemoryModel):
    """Return stable Failure identities in the same order as the writes."""

    failures: list[RecordedFailure]


class SolveAttemptParameterWrite(_MemoryModel):
    """Attribute one terminal conclusion to an exact operation input."""

    input_node_id: str = Field(min_length=1)
    cause_summary: str = Field(min_length=1)


class SolveAttemptWrite(_MemoryModel):
    """Append one terminal Solve conclusion without embedding Patch payloads."""

    operation_key: str = Field(min_length=1)
    failure_id: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    trigger_conditions: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    solution: str = Field(min_length=1)
    evidence_source: Literal["batch", "memory", "http_probe", "mixed"]
    parameters: list[SolveAttemptParameterWrite] = Field(default_factory=list)
    conflict_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_outcome(self) -> "SolveAttemptWrite":
        """Require conflict detail exactly for a conflict conclusion."""

        if (self.outcome == "conflict") != (self.conflict_reason is not None):
            raise ValueError("conflict_reason must exist exactly for conflict")
        input_ids = [item.input_node_id for item in self.parameters]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("Solve Attempt parameters must be unique")
        return self


class GeneratorChangeMemory(_MemoryModel):
    """Read one accepted deterministic Generator/Constraint diff."""

    event_id: str
    reason: str
    generator_changes: list[dict[str, Any]] = Field(default_factory=list)
    constraint_changes: list[dict[str, Any]] = Field(default_factory=list)


class SolveAttemptMemory(_MemoryModel):
    """Read one chronological terminal Solve conclusion."""

    solve_attempt_id: str
    round_number: int
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    trigger_conditions: str
    root_cause: str
    solution: str
    evidence_source: Literal["batch", "memory", "http_probe", "mixed"]
    conflict_reason: str | None = None
    parameters: list[SolveAttemptParameterWrite] = Field(default_factory=list)
    generator_change: GeneratorChangeMemory | None = None


class FailureHistory(_MemoryModel):
    """Return stable Failure metadata plus all terminal Solve Attempts."""

    failure_id: str
    summary: str
    occurrence_count: int = Field(ge=1)
    attempts: list[SolveAttemptMemory] = Field(default_factory=list)


class ParameterHistory(_MemoryModel):
    """Return Failures whose Solve conclusions attributed one exact input."""

    input_node_id: str
    failures: list[FailureHistory] = Field(default_factory=list)
