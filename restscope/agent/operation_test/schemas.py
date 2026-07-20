"""Contracts for the single-operation testing Agent."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


OperationTestStatus = Literal["passed", "failed", "errored"]
OperationTestStageStatus = Literal["passed", "failed", "errored"]
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]


class OperationTestRequest(BaseModel):
    """Input for testing exactly one OpenAPI operation."""

    task_id: str | None = None
    schema_id: str | None = None
    schema_source: dict[str, Any] | None = None
    base_url: str | None = None
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    allow_live_testing: bool = False
    max_examples: int = 20
    boundary_max_examples: int = 50
    max_failures: int = 10
    max_time: float = 120.0
    poll_interval: float | None = None
    poll_timeout: float | None = None
    seed: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_direct_target(self) -> "OperationTestRequest":
        direct_input = self.schema_source is not None and self.method is not None and self.path is not None
        if not direct_input:
            raise ValueError(
                "OperationTestRequest requires schema_source, method, and path"
            )
        if self.method is not None:
            self.method = self.method.upper()
        return self


class OperationTarget(BaseModel):
    """Resolved operation metadata used by runners."""

    schema_source: dict[str, Any]
    base_url: str | None = None
    method: str
    path: str
    operation_id: str | None = None
    schema_id: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_method(self) -> "OperationTarget":
        self.method = self.method.upper()
        return self


class StageOptions(BaseModel):
    """Per-stage limits passed to the operation test runner."""

    max_examples: int = 20
    boundary_max_examples: int = 50
    max_failures: int = 10
    max_time: float = 120.0
    poll_interval: float | None = None
    poll_timeout: float | None = None
    seed: int | None = None


class OperationTestFinding(BaseModel):
    """A compact mismatch or failure summary produced by evaluation."""

    id: str = Field(default_factory=lambda: f"finding_{uuid4().hex}")
    stage: str
    severity: FindingSeverity = "medium"
    title: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperationTestStageResult(BaseModel):
    """Sanitized result for one testing stage."""

    stage: str
    status: OperationTestStageStatus
    run_id: str | None = None
    outcome: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    failure_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[OperationTestFinding] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class OperationTestReport(BaseModel):
    """Final report returned by OperationTestAgent."""

    report_id: str = Field(default_factory=lambda: f"optest_report_{uuid4().hex}")
    status: OperationTestStatus
    task_id: str | None = None
    schema_id: str | None = None
    operation_id: str | None = None
    method: str | None = None
    path: str | None = None
    stages: list[OperationTestStageResult] = Field(default_factory=list)
    findings: list[OperationTestFinding] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
