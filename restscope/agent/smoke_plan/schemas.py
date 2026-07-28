"""Typed inputs and outputs for one Smoke planning round.

The planning boundary receives temporary case codes because a model needs a
small, unambiguous way to associate failures with cases. The public
``FailureTodo`` returned to Operation Smoke contains the complete case
evidence instead, so later Agents never have to resolve those codes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    """Use strict DTOs at the model/runtime boundary."""

    model_config = ConfigDict(extra="forbid")


class SmokePlanRequest(_Model):
    """Complete batch evidence and App-lifetime history for one Plan call."""

    operation_key: str = Field(min_length=1)
    batch: dict[str, Any]
    coded_cases: dict[str, dict[str, Any]]
    failed_case_codes: list[str]
    history: list[dict[str, Any]] = Field(default_factory=list)


class FailureTodoDecision(_Model):
    """Model-facing todo that refers to temporary codes from this request."""

    todo_id: str = Field(min_length=1, max_length=100)
    failure: str = Field(min_length=1)
    case_codes: list[str] = Field(min_length=1)


class SmokePlanDecision(_Model):
    """One complete model decision for a Smoke planning round."""

    action: Literal["process", "no_new_failure_work"]
    todos: list[FailureTodoDecision] = Field(default_factory=list)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "SmokePlanDecision":
        """Require todos only when the model elects to process failures."""
        if self.action == "process" and not self.todos:
            raise ValueError("process requires at least one todo")
        if self.action == "no_new_failure_work" and self.todos:
            raise ValueError("no_new_failure_work cannot include todos")
        return self


class FailureTodo(_Model):
    """Expanded failure work handed to a fresh Failure Solve Agent."""

    todo_id: str
    failure: str
    cases: list[dict[str, Any]] = Field(min_length=1)


class SmokeRoundPlan(_Model):
    """Usable plan or bounded failure returned to Operation Smoke."""

    status: Literal[
        "planned",
        "no_new_failure_work",
        "plan_budget_exhausted",
    ]
    todos: list[FailureTodo] = Field(default_factory=list)
    reason: str
    outputs_used: int = Field(ge=1, le=50)
