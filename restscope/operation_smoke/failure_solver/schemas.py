"""Define one Failure Solve session and its internal tool handoffs.

Solve receives one stable Failure with current Batch observations and historical
Investigations.  It may read related Parameter memory, probe the target, and ask
Parameter Patch Agent to construct validated candidates.  A final model decision
can reference only a candidate created in this session; deterministic runtime
then records or atomically applies that conclusion.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    GeneratorPatchDraft,
)
from restscope.operation_smoke.failure_dedup import FailureTodo


class _Model(BaseModel):
    """Reject extra fields so tool and final actions remain unambiguous."""

    model_config = ConfigDict(extra="forbid")


class FailureSolveRequest(_Model):
    """Supply all current evidence for one independent Investigation."""

    operation_key: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    todo: FailureTodo
    operation: dict[str, Any]
    generator_config: dict[str, Any]
    reference_options: list[dict[str, Any]] = Field(default_factory=list)


class ParameterCauseDecision(_Model):
    """Explain how one semantic input contributes to the Failure."""

    input_handle: str = Field(min_length=1)
    cause_summary: str = Field(min_length=1)


class FailureSolveDecision(_Model):
    """Return one terminal Investigation conclusion or a checkpoint choice."""

    action: Literal["apply_patch", "no_patch", "conflict", "continue"]
    candidate_ref: str | None = Field(
        default=None,
        pattern=r"^P[1-9][0-9]*$",
    )
    trigger_conditions: str | None = Field(default=None, min_length=1)
    root_cause: str | None = Field(default=None, min_length=1)
    solution: str | None = Field(default=None, min_length=1)
    evidence_source: Literal["batch", "memory", "http_probe", "mixed"] | None = None
    parameters: list[ParameterCauseDecision] = Field(default_factory=list)
    conflict_reason: str | None = Field(default=None, min_length=1)
    reason: str | None = Field(default=None, min_length=1)
    next_step: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "FailureSolveDecision":
        """Require complete durable facts for terminal decisions."""
        if self.action == "continue":
            if self.reason is None or self.next_step is None:
                raise ValueError("continue requires reason and next_step")
            forbidden = (
                self.candidate_ref,
                self.trigger_conditions,
                self.root_cause,
                self.solution,
                self.evidence_source,
                self.conflict_reason,
            )
            if any(value is not None for value in forbidden) or self.parameters:
                raise ValueError("continue cannot include terminal fields")
            return self

        required = (
            self.trigger_conditions,
            self.root_cause,
            self.solution,
            self.evidence_source,
        )
        if any(value is None for value in required):
            raise ValueError(
                "terminal decision requires trigger_conditions, root_cause, "
                "solution, and evidence_source"
            )
        if self.reason is not None or self.next_step is not None:
            raise ValueError("terminal decision cannot include continuation fields")
        if self.action == "apply_patch":
            if self.candidate_ref is None:
                raise ValueError("apply_patch requires candidate_ref")
            if self.conflict_reason is not None:
                raise ValueError("apply_patch cannot include conflict_reason")
        elif self.action == "conflict":
            if self.conflict_reason is None:
                raise ValueError("conflict requires conflict_reason")
            if self.candidate_ref is not None:
                raise ValueError("conflict cannot include candidate_ref")
        elif self.candidate_ref is not None or self.conflict_reason is not None:
            raise ValueError("no_patch cannot include candidate or conflict fields")
        return self


class PatchCandidate(_Model):
    """Keep one validated Patch private to its Solve session."""

    candidate_ref: str
    patch: GeneratorPatchDraft
    before_generators: dict[str, Any]
    after_generators: dict[str, Any]
    samples: list[dict[str, Any]]
    patch_outputs: int = Field(ge=1, le=20)


class FailureSolveOutcome(_Model):
    """Return one persisted terminal result or bounded Solve exhaustion."""

    status: Literal[
        "applied_patch",
        "no_patch",
        "conflict",
        "solve_budget_exhausted",
    ]
    outputs_used: int = Field(ge=1, le=50)
    investigation_id: str | None = None
    active_config_revision: int | None = Field(default=None, ge=1)
    applied_patch: PatchCandidate | None = None
    active_constraints: list[CompiledConstraintPatch] = Field(
        default_factory=list
    )
    reason: str | None = None
