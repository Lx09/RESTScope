"""Public contracts for the LLM-led Operation Smoke lifecycle.

The coordinator exposes bounded summaries of rounds, todos, Patch attempts,
and effect decisions. Complete requests and responses remain only in the
App-lifetime ledger because they may contain sensitive target data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from restscope.testing import OperationExecutionReport


class _PublicModel(BaseModel):
    """Keep public Smoke handoffs immutable and reject accidental old fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OperationSmokeRequest(_PublicModel):
    """Configure one large-turn Smoke attempt for one operation."""

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    seed: int | None = Field(default=None, ge=0)
    max_plan_outputs: int = Field(default=50, ge=1, le=50)
    max_solve_outputs_per_todo: int = Field(default=50, ge=1, le=50)
    max_patch_outputs: int = Field(default=20, ge=1, le=20)
    max_effect_outputs: int = Field(default=2, ge=1, le=2)
    continuation_interval: int = Field(default=10, ge=1, le=50)


TodoFinishStatus = Literal[
    "resolved",
    "already_absent",
    "non_parameter",
    "dependency_related",
    "insufficient_evidence",
    "no_new_attempt",
    "solve_budget_exhausted",
]


class PatchAttemptSummary(_PublicModel):
    """Bounded public record for one Patch Agent and optional Effect decision."""

    patch_outputs: int = Field(ge=1, le=20)
    patch_status: Literal["validated", "failed"]
    effect_outcome: Literal[
        "resolved_without_regression",
        "unresolved",
        "regression",
        "unknown",
    ] | None = None
    effect_outputs: int | None = Field(default=None, ge=1, le=2)
    accepted: bool = False


class TodoRunSummary(_PublicModel):
    """Public result of one fixed-round todo and all its Patch attempts."""

    todo_id: str = Field(min_length=1)
    failure: str = Field(min_length=1)
    status: TodoFinishStatus
    solve_outputs: int = Field(ge=1, le=50)
    patch_attempts: list[PatchAttemptSummary] = Field(default_factory=list)


class SmokeRoundSummary(_PublicModel):
    """Public record for one full-batch Plan round."""

    round_number: int = Field(ge=1)
    baseline_run_id: str = Field(min_length=1)
    plan_status: Literal[
        "planned",
        "no_new_failure_work",
        "plan_budget_exhausted",
    ]
    plan_outputs: int = Field(ge=1, le=50)
    todos: list[TodoRunSummary] = Field(default_factory=list)


class OperationSmokeResult(_PublicModel):
    """Supervisor-facing outcome based only on the latest complete batch."""

    status: Literal["passed", "retry", "unsupported", "errored"]
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    active_config_revision: int = Field(ge=1)
    batch_reports: list[OperationExecutionReport] = Field(default_factory=list)
    rounds: list[SmokeRoundSummary] = Field(default_factory=list)
    failure_kind: Literal[
        "no_new_failure_work",
        "plan_budget_exhausted",
        "unsupported_operation",
        "operation_error",
    ] | None = None
    error: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_status_pair(self) -> "OperationSmokeResult":
        """Keep status and stop reason unambiguous for Supervisor scheduling."""
        allowed = {
            "passed": {None},
            "retry": {
                "no_new_failure_work",
                "plan_budget_exhausted",
            },
            "unsupported": {"unsupported_operation"},
            "errored": {"operation_error"},
        }
        if self.failure_kind not in allowed[self.status]:
            raise ValueError(
                f"{self.status} has incompatible failure_kind "
                f"{self.failure_kind!r}"
            )
        return self
