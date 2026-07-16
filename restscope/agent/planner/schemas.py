"""Public contracts for Planner-generated test requirements."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RequirementKind = Literal["single_operation", "workflow"]
RequirementPriority = Literal["critical", "high", "medium", "low"]


class SingleOperationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    operation_id: str = Field(min_length=1)
    data_dependency: str | None = None


class WorkflowTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[WorkflowStep] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_order(self) -> "WorkflowTarget":
        orders = [step.order for step in self.steps]
        if orders != list(range(1, len(self.steps) + 1)):
            raise ValueError("workflow step order must be contiguous and start at 1")
        return self


class TestRequirementDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RequirementKind
    title: str = Field(min_length=1)
    priority: RequirementPriority
    objective: str = Field(min_length=1)
    target: SingleOperationTarget | WorkflowTarget
    test_focus: list[str] = Field(min_length=1)
    expected_behaviors: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target_kind(self) -> "TestRequirementDraft":
        if self.kind == "single_operation" and not isinstance(self.target, SingleOperationTarget):
            raise ValueError("single_operation requires SingleOperationTarget")
        if self.kind == "workflow" and not isinstance(self.target, WorkflowTarget):
            raise ValueError("workflow requires WorkflowTarget")
        return self


class TestRequirementPlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[TestRequirementDraft] = Field(min_length=1)


class TestRequirement(TestRequirementDraft):
    requirement_id: str


class TestRequirementPlan(BaseModel):
    plan_id: str
    task_id: str
    schema_id: str
    revision: int = Field(ge=1)
    previous_plan_id: str | None = None
    generated_at: datetime
    requirements: list[TestRequirement] = Field(min_length=1)


class PlannerRequest(BaseModel):
    task_id: str = Field(min_length=1)


class PlannerResult(BaseModel):
    plan: TestRequirementPlan
    artifact_id: str
    context_snapshot_id: str
