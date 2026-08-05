"""Define stable Failure and terminal Resolution Attempt persistence contracts.

Failure Resolution finalization writes decided semantic groups without storing
Test Cases and appends terminal conclusions with optional input attribution.
Accepted Generator/Constraint diffs are exposed with their terminal Attempt, while
Patch samples, response bodies, and Agent transcripts never cross this seam.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _MemoryModel(BaseModel):
    """Reject accidental persistence fields at the workflow boundary."""

    model_config = ConfigDict(extra="forbid")


class FailureWrite(_MemoryModel):
    """Describe one stable Failure occurrence from a final Resolution item."""

    summary: str = Field(min_length=1)
    messages: list[str] = Field(min_length=1, max_length=100)
    suspected_input_node_ids: list[str]
    last_status_code: int | None = Field(default=None, ge=100, le=599)

    @model_validator(mode="after")
    def validate_sets(self) -> "FailureWrite":
        """Require unique messages and suspected input identities."""

        if len(self.messages) != len(set(self.messages)):
            raise ValueError("Failure messages must be unique")
        if len(self.suspected_input_node_ids) != len(
            set(self.suspected_input_node_ids)
        ):
            raise ValueError("suspected input node IDs must be unique")
        return self


class FailureBatchWrite(_MemoryModel):
    """Record validated Failure groups selected by one final worklist."""

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
    """Append one terminal conclusion with only facts trustworthy for it.

    Failure Resolution supplies the final root cause and reason for both
    outcomes. Applied-Patch Parameter attribution comes from the immutable
    candidate; no-Patch attribution comes from validated worklist handles.
    The older ``conflict`` storage value remains available to the lower-level
    optimistic Patch seam and uses candidate-derived facts.
    """

    operation_key: str = Field(min_length=1)
    failure_id: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    reason: str = Field(min_length=1)
    root_cause: str | None = None
    parameters: list[SolveAttemptParameterWrite] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome(self) -> "SolveAttemptWrite":
        """Reject duplicate Parameter attribution within one Attempt."""

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
    """Read one chronological terminal Resolution conclusion."""

    solve_attempt_id: str
    round_number: int
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    reason: str
    root_cause: str | None = None
    parameters: list[SolveAttemptParameterWrite] = Field(default_factory=list)
    generator_change: GeneratorChangeMemory | None = None


class FailureHistory(_MemoryModel):
    """Return stable Failure metadata plus all terminal Resolution Attempts."""

    failure_id: str
    summary: str
    occurrence_count: int = Field(ge=1)
    attempts: list[SolveAttemptMemory] = Field(default_factory=list)


class ParameterHistory(_MemoryModel):
    """Return Failures whose Resolution conclusions attributed one exact input."""

    input_node_id: str
    failures: list[FailureHistory] = Field(default_factory=list)
