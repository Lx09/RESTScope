"""Define the public, bounded result of the Operation Smoke workflow.

Full Batch requests/responses and LLM conversations remain inside their owning
runtime boundaries.  The public result reports actual Batch success, why the
workflow stopped, and what each Investigation applied or declined.  It never
labels a Patch ``resolved`` because only a later complete Batch can demonstrate
its real effect.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.testing import OperationExecutionReport


class _PublicModel(BaseModel):
    """Make public Smoke contracts immutable and reject removed old fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationSmokeRequest(_PublicModel):
    """Configure bounded Batch and Agent work for one operation."""

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    max_dedup_outputs: int = Field(default=50, ge=1, le=50)
    max_solve_outputs_per_todo: int = Field(default=50, ge=1, le=50)
    max_patch_outputs: int = Field(default=20, ge=1, le=20)
    continuation_interval: int = Field(default=10, ge=1, le=50)


OperationSmokeStopReason: TypeAlias = Literal[
    "success_rate_reached",
    "no_patch_applied",
]
OperationSmokeStatus: TypeAlias = Literal["passed", "unsupported", "errored"]
OperationSmokeFailureKind: TypeAlias = Literal[
    "dedup_budget_exhausted",
    "solve_budget_exhausted",
    "unsupported_operation",
    "operation_error",
]


class PatchAttemptSummary(_PublicModel):
    """Describe the selected validated candidate actually applied by Solve."""

    candidate_ref: str
    patch_outputs: int = Field(ge=1, le=20)
    applied_revision: int = Field(ge=1)
    changed_input_count: int = Field(ge=0)
    constraint_count: int = Field(ge=0)


class TodoRunSummary(_PublicModel):
    """Summarize one independent Failure Investigation."""

    todo_id: str = Field(min_length=1)
    failure: str = Field(min_length=1)
    status: Literal["applied_patch", "no_patch", "conflict"]
    solve_outputs: int = Field(ge=1, le=50)
    investigation_id: str | None = None
    reason: str | None = None
    applied_patch: PatchAttemptSummary | None = None

    @model_validator(mode="after")
    def validate_patch_summary(self) -> "TodoRunSummary":
        """Require an applied summary exactly for applied-Patch outcomes."""
        if self.status == "applied_patch" and self.applied_patch is None:
            raise ValueError("applied_patch status requires applied_patch")
        if self.status != "applied_patch" and self.applied_patch is not None:
            raise ValueError("only applied_patch status may include applied_patch")
        return self


class SmokeRoundSummary(_PublicModel):
    """Record one Batch, its Dedup result, and completed Investigations."""

    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1)
    dedup_status: Literal[
        "bypassed",
        "deduplicated",
        "dedup_budget_exhausted",
    ]
    dedup_outputs: int = Field(ge=0, le=50)
    failure_count: int = Field(default=0, ge=0)
    todos: list[TodoRunSummary] = Field(default_factory=list)


class OperationSmokeResult(_PublicModel):
    """Return actual success metrics and an explicit terminal explanation."""

    status: OperationSmokeStatus
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    active_config_revision: int = Field(ge=1)
    batch_reports: list[OperationExecutionReport] = Field(default_factory=list)
    rounds: list[SmokeRoundSummary] = Field(default_factory=list)
    stop_reason: OperationSmokeStopReason | None = None
    reason: str | None = None
    failure_kind: OperationSmokeFailureKind | None = None
    error: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "OperationSmokeResult":
        """Keep passed, unsupported, and technical-error results distinct."""
        if self.status == "passed":
            if self.stop_reason is None or self.reason is None:
                raise ValueError("passed result requires stop_reason and reason")
            if self.failure_kind is not None or self.error is not None:
                raise ValueError("passed result cannot include an error")
        elif self.status == "unsupported":
            if self.failure_kind != "unsupported_operation":
                raise ValueError(
                    "unsupported result requires unsupported_operation"
                )
            if self.stop_reason is not None:
                raise ValueError("unsupported result cannot have stop_reason")
        else:
            if self.failure_kind not in {
                "dedup_budget_exhausted",
                "solve_budget_exhausted",
                "operation_error",
            }:
                raise ValueError("errored result requires a technical failure_kind")
            if self.stop_reason is not None or self.error is None:
                raise ValueError(
                    "errored result requires error and no passed stop_reason"
                )
        return self
