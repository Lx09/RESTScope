"""Public contracts for supervisor-level RESTScope runs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from ..operation_test import OperationTestFinding, OperationTestReport, OperationTestStatus


SupervisorTaskKind = Literal["operation_test"]


class OperationSelection(BaseModel):
    """One operation selected for supervisor-level testing."""

    method: str
    path: str
    operation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_method(self) -> "OperationSelection":
        self.method = self.method.upper()
        return self


class RESTScopeRunRequest(BaseModel):
    """Direct supervisor input for one RESTScope run."""

    task_kind: SupervisorTaskKind = "operation_test"
    schema_source: dict[str, Any]
    base_url: str | None = None
    operations: list[OperationSelection] = Field(default_factory=list)
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


class RESTScopeRunReport(BaseModel):
    """Supervisor-level report for a full RESTScope run."""

    report_id: str = Field(default_factory=lambda: f"restscope_run_{uuid4().hex}")
    status: OperationTestStatus
    task_kind: SupervisorTaskKind = "operation_test"
    operations: list[OperationSelection] = Field(default_factory=list)
    operation_reports: list[OperationTestReport] = Field(default_factory=list)
    findings: list[OperationTestFinding] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
