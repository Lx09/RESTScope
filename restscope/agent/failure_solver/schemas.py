"""Typed inputs and decisions for one continuous failure investigation.

The Failure Solve Agent is the only component that diagnoses causes and writes
Patch requirements. Its request contains expanded evidence rather than the
temporary codes used by Smoke Plan.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.agent.smoke_plan import FailureTodo


class _Model(BaseModel):
    """Reject extra model fields at every Solve boundary."""

    model_config = ConfigDict(extra="forbid")


class FailureSolveRequest(_Model):
    """Complete starting context for one fresh todo conversation."""

    operation_key: str = Field(min_length=1)
    todo: FailureTodo
    operation: dict[str, Any]
    generator_config: dict[str, Any]
    current_batch: dict[str, Any]
    reference_options: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)


class PatchRequirement(_Model):
    """Root cause and desired behavior handed to Parameter Patch."""

    root_cause: str = Field(min_length=1)
    affected_inputs: list[str] = Field(min_length=1)
    desired_behavior: str = Field(min_length=1)
    acceptance_criteria: str = Field(min_length=1)


class FailureSolveDecision(_Model):
    """One structured decision when the model does not call HTTP."""

    action: Literal["patch_ready", "finish", "continue"]
    patch_requirement: PatchRequirement | None = None
    finish_status: Literal[
        "already_absent",
        "non_parameter",
        "dependency_related",
        "insufficient_evidence",
        "no_new_attempt",
    ] | None = None
    reason: str | None = Field(default=None, min_length=1)
    next_step: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "FailureSolveDecision":
        """Require exactly the fields that make the selected action usable."""
        if self.action == "patch_ready":
            if self.patch_requirement is None:
                raise ValueError("patch_ready requires patch_requirement")
            if self.finish_status is not None or self.next_step is not None:
                raise ValueError("patch_ready cannot include finish fields")
        elif self.action == "finish":
            if self.finish_status is None or self.reason is None:
                raise ValueError("finish requires finish_status and reason")
            if self.patch_requirement is not None or self.next_step is not None:
                raise ValueError("finish cannot include patch or next_step")
        else:
            if self.reason is None or self.next_step is None:
                raise ValueError("continue requires reason and next_step")
            if self.patch_requirement is not None or self.finish_status is not None:
                raise ValueError("continue cannot include finish or patch fields")
        return self


class FailureSolveOutcome(_Model):
    """Patch handoff, terminal todo status, or bounded exhaustion."""

    status: Literal[
        "patch_ready",
        "already_absent",
        "non_parameter",
        "dependency_related",
        "insufficient_evidence",
        "no_new_attempt",
        "solve_budget_exhausted",
    ]
    outputs_used: int = Field(ge=1, le=50)
    patch_requirement: PatchRequirement | None = None
    reason: str | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)
    output_history: list[dict[str, Any]] = Field(default_factory=list)
