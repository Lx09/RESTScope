"""Contracts for one operation-test attempt and dependency analysis."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


OperationTestStatus = Literal["passed", "failed", "errored"]
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]


class OperationReference(BaseModel):
    """Stable operation identity shared by OperationTestAgent and Supervisor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str
    path: str
    operation_id: str | None = None

    @model_validator(mode="after")
    def normalize(self) -> "OperationReference":
        object.__setattr__(self, "method", self.method.upper())
        if not self.path.startswith("/"):
            raise ValueError("operation path must start with '/'")
        return self

    def identity(self) -> tuple[str, str, str | None]:
        return (self.method, self.path, self.operation_id)


class OperationCandidate(BaseModel):
    """Compact, schema-derived operation context safe for dependency analysis."""

    operation: OperationReference
    summary: str | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    security: list[dict[str, Any]] = Field(default_factory=list)
    request_structure: dict[str, Any] | None = None
    response_structure: dict[str, Any] = Field(default_factory=dict)


class FailureSummary(BaseModel):
    """Small failure projection read from the MCP failure store."""

    failure_id: str
    check: str
    title: str
    message: str
    response_status: int | None = None


class OperationExecutionResult(BaseModel):
    """Result of exactly one Schemathesis run."""

    run_id: str
    outcome: str
    status_code_counts: dict[str, int] = Field(default_factory=dict)
    failure_ids: list[str] = Field(default_factory=list)
    failure_summaries: list[FailureSummary] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str | None = None

    @property
    def observed_2xx(self) -> bool:
        return any(
            count > 0 and status.isdigit() and 200 <= int(status) < 300
            for status, count in self.status_code_counts.items()
        )


class OperationDependencyAnalysis(BaseModel):
    """Structured LLM output containing direct dependencies only."""

    model_config = ConfigDict(extra="forbid")

    dependency_issue: StrictBool
    hint: str | None = None
    dependencies: list[OperationReference] = Field(default_factory=list)


class OperationTestRequest(BaseModel):
    """Input for one complete operation-test attempt."""

    task_id: str | None = None
    schema_source: dict[str, Any]
    base_url: str | None = None
    operation: OperationReference
    candidate_operations: list[OperationCandidate]
    headers: dict[str, str] = Field(default_factory=dict)
    allow_live_testing: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_current_candidate(self) -> "OperationTestRequest":
        identities = {candidate.operation.identity() for candidate in self.candidate_operations}
        if self.operation.identity() not in identities:
            raise ValueError("current operation must be present in candidate_operations")
        return self


class OperationTarget(BaseModel):
    """Runtime target passed to the Schemathesis runner."""

    schema_source: dict[str, Any]
    base_url: str | None = None
    operation: OperationReference
    headers: dict[str, str] = Field(default_factory=dict)


class OperationTestFinding(BaseModel):
    """Compact finding derived from one Schemathesis result."""

    id: str = Field(default_factory=lambda: f"finding_{uuid4().hex}")
    severity: FindingSeverity = "medium"
    title: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationTestReport(BaseModel):
    """Final report returned for one attempt."""

    report_id: str = Field(default_factory=lambda: f"optest_report_{uuid4().hex}")
    status: OperationTestStatus
    task_id: str | None = None
    operation: OperationReference
    execution: OperationExecutionResult | None = None
    dependency_analysis: OperationDependencyAnalysis | None = None
    observed_2xx: bool = False
    findings: list[OperationTestFinding] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def method(self) -> str:
        return self.operation.method

    @property
    def path(self) -> str:
        return self.operation.path

    @property
    def operation_id(self) -> str | None:
        return self.operation.operation_id
