"""Define the bounded public result of the Operation Smoke workflow.

Full Batch requests/responses, candidate registries, worklists, and LLM
conversations remain inside their run-local owners. The public result reports
actual Batch success and the durable decisions produced by one continuous
Failure Resolution session. It never labels a Patch ``resolved`` because only
a later complete Batch can demonstrate its real effect.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _PublicModel(BaseModel):
    """Make public Smoke contracts immutable and reject removed old fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationSmokeRequest(_PublicModel):
    """Configure deterministic Batch work for one operation.

    Agent, Patch, and Review quotas are intentionally absent. One internal
    1000-output hard guard covers the complete Operation Smoke execution.
    """

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)


OperationSmokeStopReason: TypeAlias = Literal[
    "success_rate_reached",
    "no_patch_applied",
]
OperationSmokeStatus: TypeAlias = Literal["passed", "unsupported", "errored"]
OperationSmokeFailureKind: TypeAlias = Literal[
    "failure_resolution_limit_exceeded",
    "unsupported_operation",
    "operation_error",
    "provider_unavailable",
]


class ResolutionPatchSummary(_PublicModel):
    """Describe one registry candidate actually applied by final Resolution."""

    candidate_ref: str = Field(pattern=r"^P[1-9][0-9]*$")
    patch_outputs: int = Field(ge=1, le=1_000)
    generator_change_event_id: str = Field(min_length=1)
    changed_input_count: int = Field(ge=0)
    constraint_count: int = Field(ge=0)


class ResolutionItemSummary(_PublicModel):
    """Summarize one decided final worklist item and its durable Attempt."""

    item_id: str = Field(min_length=1)
    failure_summary: str = Field(min_length=1)
    outcome: Literal["apply_patch", "no_patch"]
    attempt_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    applied_patch: ResolutionPatchSummary | None = None

    @model_validator(mode="after")
    def validate_patch_summary(self) -> "ResolutionItemSummary":
        """Require an applied summary exactly for apply-Patch outcomes."""
        if self.outcome == "apply_patch" and self.applied_patch is None:
            raise ValueError("apply_patch outcome requires applied_patch")
        if self.outcome == "no_patch" and self.applied_patch is not None:
            raise ValueError("no_patch outcome cannot include applied_patch")
        return self


class SmokeRoundSummary(_PublicModel):
    """Record one Batch and its one continuous Failure Resolution result."""

    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1)
    resolution_status: Literal["completed"]
    resolution_outputs: int = Field(ge=1, le=1_000)
    failure_count: int = Field(default=0, ge=0)
    items: list[ResolutionItemSummary] = Field(default_factory=list)


class OperationSmokeResult(_PublicModel):
    """Return actual success metrics and an explicit terminal explanation."""

    status: OperationSmokeStatus
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    batch_run_ids: list[str] = Field(default_factory=list)
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
                "failure_resolution_limit_exceeded",
                "operation_error",
                "provider_unavailable",
            }:
                raise ValueError("errored result requires a technical failure_kind")
            if self.stop_reason is not None or self.error is None:
                raise ValueError(
                    "errored result requires error and no passed stop_reason"
                )
        return self
