"""Public contracts for round-based Supervisor runs."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..operation_test import OperationReference, OperationTestFinding, OperationTestReport


RunStatus = Literal["passed", "failed", "errored"]
AttemptDisposition = Literal[
    "satisfied",
    "blocked",
    "retrying",
    "unsupported",
    "failed",
    "errored",
]
OperationFailureKind = Literal[
    "threshold_exhausted",
    "no_parameter_issue",
    "unsupported_operation",
    "operation_error",
    "operation_failed",
]
StopReason = Literal[
    "completed",
    "completed_with_failures",
    "operation_failed",
    "unresolved_dependencies",
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
    """The only public Supervisor input for an MVP run."""

    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    max_operation_attempts: int = Field(default=3, ge=1, le=10)


class OperationAttempt(BaseModel):
    """Chronological evidence retained for every operation execution."""

    operation: OperationReference
    round_number: int
    attempt_number: int
    report: OperationTestReport
    disposition: AttemptDisposition
    failure_kind: OperationFailureKind | None = None
    dependency_hint: str | None = None
    direct_dependencies: list[OperationReference] = Field(default_factory=list)
    unsatisfied_dependencies: list[OperationReference] = Field(default_factory=list)


class BlockedOperation(BaseModel):
    """Latest unresolved scheduling state for an attempted operation."""

    operation: OperationReference
    dependency_hint: str | None = None
    direct_dependencies: list[OperationReference] = Field(default_factory=list)
    unsatisfied_dependencies: list[OperationReference] = Field(default_factory=list)
    reason: Literal["unknown_dependency", "failed_prerequisite", "dependency_cycle", "unsatisfied_dependency"]


class RESTScopeRunReport(BaseModel):
    """Complete scheduler result without secrets or queue internals."""

    report_id: str = Field(default_factory=lambda: f"restscope_run_{uuid4().hex}")
    status: RunStatus
    stop_reason: StopReason
    operations: list[OperationReference] = Field(default_factory=list)
    attempts: list[OperationAttempt] = Field(default_factory=list)
    satisfied_operations: list[OperationReference] = Field(default_factory=list)
    blocked_operations: list[BlockedOperation] = Field(default_factory=list)
    unattempted_operations: list[OperationReference] = Field(default_factory=list)
    dependency_cycles: list[list[OperationReference]] = Field(default_factory=list)
    findings: list[OperationTestFinding] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    rounds: int = 0
    attempt_count: int = 0
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
