"""Define public input and output contracts for a Supervisor run.

The Supervisor receives one bounded run request and returns chronological
Operation Smoke attempts plus final status, stop reason, and unattempted
operations. These DTOs cross the top-level App boundary but do not persist the
runtime scheduler queue.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from restscope.operations import OperationReference

from ..operation_smoke import OperationSmokeResult


RunStatus = Literal["passed", "failed", "errored"]
AttemptDisposition = Literal[
    "satisfied",
    "retrying",
    "unsupported",
    "failed",
    "errored",
]
OperationFailureKind = Literal[
    "dedup_budget_exhausted",
    "solve_budget_exhausted",
    "unsupported_operation",
    "operation_error",
    "provider_unavailable",
]
StopReason = Literal[
    "completed",
    "completed_with_failures",
    "technical_error",
]


class FileSchemaSource(BaseModel):
    """
    Carry validated file schema source data across the dynamic top-level operation
    scheduling loop.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file"]
    path: str


class UrlSchemaSource(BaseModel):
    """
    Carry validated url schema source data across the dynamic top-level operation
    scheduling loop.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["url"]
    url: str


class InlineSchemaSource(BaseModel):
    """
    Carry validated inline schema source data across the dynamic top-level operation
    scheduling loop.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline"]
    format: Literal["yaml", "json"] = "yaml"
    content: str


SchemaSource = Annotated[
    FileSchemaSource | UrlSchemaSource | InlineSchemaSource,
    Field(discriminator="kind"),
]


class RESTScopeRunRequest(BaseModel):
    """Public input for one bounded Supervisor run."""

    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    max_operation_attempts: int = Field(default=3, ge=1, le=10)


class OperationAttempt(BaseModel):
    """Chronological result of one Operation Smoke invocation."""

    model_config = ConfigDict(extra="forbid")

    operation: OperationReference
    round_number: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    smoke_result: OperationSmokeResult
    disposition: AttemptDisposition
    failure_kind: OperationFailureKind | None = None


class RESTScopeRunReport(BaseModel):
    """Complete Smoke-only Supervisor result."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(default_factory=lambda: f"restscope_run_{uuid4().hex}")
    random_seed: int = Field(ge=0)
    status: RunStatus
    stop_reason: StopReason
    operations: list[OperationReference] = Field(default_factory=list)
    attempts: list[OperationAttempt] = Field(default_factory=list)
    satisfied_operations: list[OperationReference] = Field(default_factory=list)
    unattempted_operations: list[OperationReference] = Field(default_factory=list)
    rounds: int = 0
    attempt_count: int = 0
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
