"""Public contracts for round-based Operation Smoke runs."""

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
    "threshold_exhausted",
    "no_parameter_issue",
    "diagnosis_inconclusive",
    "unsupported_operation",
    "operation_error",
]
StopReason = Literal[
    "completed",
    "completed_with_failures",
    "technical_error",
]


class FileSchemaSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["file"]
    path: str


class UrlSchemaSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["url"]
    url: str


class InlineSchemaSource(BaseModel):
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
