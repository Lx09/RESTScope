"""Define the Planner boundary for one complete Operation Smoke Batch.

Planner receives bounded failed-case evidence from the Coordinator. Runtime
retrieval selects a small same-operation candidate window and replaces database
identities with request-local Failure references such as ``F1``. Planner has no
tools; deterministic code records its validated classification and expands it
into independent Failure work items for Solve.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    """Reject unrecognized fields at every Planner/model boundary."""

    model_config = ConfigDict(extra="forbid")


class SmokePlanRequest(_Model):
    """Supply all current evidence required to classify a Batch.

    ``coded_cases`` contains both successful and failed bounded evidence, while
    ``failed_case_codes`` identifies the observations Planner must account for.
    The codes are request-local aliases and are never persisted.
    """

    operation_key: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1)
    batch: dict[str, Any]
    coded_cases: dict[str, dict[str, Any]]
    failed_case_codes: list[str]


class FailureClassificationDecision(_Model):
    """Classify current observations under one existing or new Failure."""

    item_id: str = Field(min_length=1, max_length=100)
    failure_ref: str | None = Field(
        default=None,
        pattern=r"^F[1-9][0-9]*$",
    )
    summary: str = Field(min_length=1)
    case_codes: list[str] = Field(min_length=1)
    disposition: Literal["debug", "non_debuggable"] = "debug"
    disposition_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_disposition(self) -> "FailureClassificationDecision":
        """Require a reason only when the classification will not be solved."""
        if self.disposition == "non_debuggable" and not self.disposition_reason:
            raise ValueError("non_debuggable requires disposition_reason")
        if self.disposition == "debug" and self.disposition_reason is not None:
            raise ValueError("debug classification cannot have disposition_reason")
        return self


class SmokePlanDecision(_Model):
    """Represent one complete semantic classification of current failures."""

    action: Literal["process", "no_debug"]
    classifications: list[FailureClassificationDecision] = Field(
        default_factory=list
    )
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "SmokePlanDecision":
        """Keep the no-debug and processing result shapes unambiguous.

        ``no_debug`` still classifies every current Observation so Memory does
        not lose evidence; each classification must explicitly explain why it
        should not open a Solve session.
        """
        if self.action == "process" and not self.classifications:
            raise ValueError("process requires at least one classification")
        if self.action == "no_debug" and any(
            item.disposition != "non_debuggable"
            for item in self.classifications
        ):
            raise ValueError(
                "no_debug classifications must all be non_debuggable"
            )
        return self


class FailureTodo(_Model):
    """Hand one stable Failure and its current observations to Solve.

    ``failure_id`` is runtime-only storage identity.  Failure Solve excludes it
    from model prompts and uses it only for deterministic history reads/writes.
    """

    todo_id: str
    failure_id: str
    failure: str
    cases: list[dict[str, Any]] = Field(min_length=1)


class NonDebuggableFailure(_Model):
    """Retain Planner's explicit reason for not opening a Solve session."""

    failure_id: str
    failure: str
    reason: str
    cases: list[dict[str, Any]] = Field(min_length=1)


class SmokeRoundPlan(_Model):
    """Return usable work, a no-debug stop, or bounded Agent exhaustion."""

    status: Literal["planned", "no_debug", "plan_budget_exhausted"]
    todos: list[FailureTodo] = Field(default_factory=list)
    non_debuggable: list[NonDebuggableFailure] = Field(default_factory=list)
    reason: str
    outputs_used: int = Field(ge=1, le=50)
