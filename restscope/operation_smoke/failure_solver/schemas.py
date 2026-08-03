"""Define one Failure Solve session and its internal tool handoffs.

Solve receives one stable Failure with current Batch evidence and historical
Solve Attempts.  It may read related Parameter memory, probe the target, and ask
Parameter Patch Agent to construct validated candidates.  A final model decision
can reference only a candidate created in this session; deterministic runtime
then records or atomically applies that conclusion.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.operation_smoke.memory import SolveAttemptParameterWrite
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    GeneratorPatchDraft,
)
from restscope.operation_smoke.failure_dedup import FailureTodo


class _Model(BaseModel):
    """Reject extra fields so tool and final actions remain unambiguous."""

    model_config = ConfigDict(extra="forbid")


class FailureSolveRequest(_Model):
    """Supply all current evidence for one independent Solve session."""

    operation_key: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    todo: FailureTodo
    operation: dict[str, Any]
    generator_config: dict[str, Any]
    reference_options: list[dict[str, Any]] = Field(default_factory=list)


class FailureSolveDecision(_Model):
    """Choose one terminal outcome without repeating candidate-owned facts.

    Action-specific checks intentionally live in the Solve session. Keeping
    this DTO flat gives OpenAI-compatible providers one ordinary object Schema
    while deterministic runtime decides whether the supplied reference or
    reason matters for the selected action.
    """

    action: Literal["apply_patch", "no_patch"]
    candidate_ref: str | None = None
    reason: str | None = None


class PatchCandidate(_Model):
    """Keep one reviewed Patch and its derived facts in one Solve session.

    The Patch task supplied the cause, desired behavior, and affected inputs to
    both the Patch Agent and its independent Reviewer. Storing those facts on
    the candidate lets deterministic runtime persist the selected evidence
    without asking the Solve model to restate it in terminal JSON.
    """

    candidate_ref: str
    patch: GeneratorPatchDraft
    root_cause: str
    change_reason: str
    parameter_attributions: list[SolveAttemptParameterWrite]
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
    solve_attempt_id: str | None = None
    generator_change_event_id: str | None = None
    applied_patch: PatchCandidate | None = None
    active_constraints: list[CompiledConstraintPatch] = Field(
        default_factory=list
    )
    reason: str | None = None

    @model_validator(mode="after")
    def validate_persistence_identity(self) -> "FailureSolveOutcome":
        """Keep terminal persistence IDs separate from technical exhaustion.

        Every terminal Solve conclusion is durable and therefore has one
        ``solve_attempt_id``. Only an applied Patch has a Generator change
        event and selected candidate. Budget exhaustion writes nothing.
        """

        if self.status == "solve_budget_exhausted":
            if (
                self.solve_attempt_id is not None
                or self.generator_change_event_id is not None
                or self.applied_patch is not None
                or self.active_constraints
            ):
                raise ValueError("Solve budget exhaustion cannot include persisted state")
            if self.reason is None:
                raise ValueError("Solve budget exhaustion requires a reason")
            return self

        if self.solve_attempt_id is None:
            raise ValueError("A terminal Solve outcome requires solve_attempt_id")
        if self.status == "applied_patch":
            if self.generator_change_event_id is None or self.applied_patch is None:
                raise ValueError(
                    "An applied Patch requires its candidate and Generator change event"
                )
        elif (
            self.generator_change_event_id is not None
            or self.applied_patch is not None
            or self.active_constraints
        ):
            raise ValueError("Only an applied Patch may include changed current state")
        return self
