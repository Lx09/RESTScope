"""Define the current-Batch Failure deduplication Interface.

Batch evidence enters through :class:`FailureDedupRequest`. The deterministic
Deduplicator removes exact duplicate messages, optionally asks the LLM Agent to
group the remainder, persists validated Failures, and returns one
:class:`FailureTodo` per Failure for the Solve stage.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    """Reject unexpected fields at every model and runtime seam."""

    model_config = ConfigDict(extra="forbid")


class FailureDedupRequest(_Model):
    """Identify failed Catalog cases from one complete Batch round."""

    operation_key: str = Field(min_length=1)
    round_number: int = Field(ge=1)
    batch_run_id: str = Field(min_length=1)
    case_ids: list[str] = Field(min_length=1)
    input_node_ids_by_handle: dict[str, str]


class FailureGroupDecision(_Model):
    """Group exact messages under one provisional causal Parameter set."""

    summary: str = Field(min_length=1)
    suspected_parameters: list[str]
    messages: list[str] = Field(min_length=1)


class FailureDedupDecision(_Model):
    """Represent one complete replacement classification from the Agent."""

    failures: list[FailureGroupDecision] = Field(min_length=1)
    reason: str = Field(min_length=1)


class FailureTodo(_Model):
    """Give Solve one stable Failure and exactly one reproducible test case.

    ``suspected_parameters`` is ``None`` when the single-Fingerprint fast path
    skipped LLM attribution. An empty list has a different meaning: the Agent
    classified the Failure as operation-level rather than Parameter-caused.
    """

    todo_id: str
    failure_id: str
    failure: str
    test_case_id: str = Field(pattern=r"^TC[1-9][0-9]*$")
    suspected_parameters: list[str] | None = None


class FailureDedupResult(_Model):
    """Return usable Solve work or a bounded Agent-exhaustion result."""

    status: Literal["bypassed", "deduplicated", "dedup_budget_exhausted"]
    todos: list[FailureTodo] = Field(default_factory=list)
    reason: str
    outputs_used: int = Field(ge=0, le=50)
    exact_fingerprint_count: int = Field(ge=1)
    correction_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status_shape(self) -> "FailureDedupResult":
        """Keep successful and exhausted result shapes unambiguous."""
        if self.status == "dedup_budget_exhausted" and self.todos:
            raise ValueError("an exhausted Dedup result cannot contain todos")
        if self.status != "dedup_budget_exhausted" and not self.todos:
            raise ValueError("a successful Dedup result requires at least one todo")
        if self.status == "bypassed" and self.outputs_used != 0:
            raise ValueError("the bypass path cannot consume an LLM output")
        return self
