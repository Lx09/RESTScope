"""Pydantic schemas for Context packages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ContextRole = Literal["planner", "result_analyst", "decision_maker", "check_designer", "intelligence_updater"]
ContextSectionKind = Literal[
    "role_instruction",
    "task_state",
    "test_goal",
    "budget",
    "operation_targets",
    "operation_risk_profile",
    "historical_observations",
    "campaign_history",
    "current_campaign_result",
    "recent_events",
    "learned_constraints",
    "available_checks",
    "testing_knowledge",
    "tool_affordances",
    "execution_assumptions",
    "output_contract",
]
MessageRole = Literal["system", "user", "assistant"]


class SourceRef(BaseModel):
    source_table: str
    source_id: str
    field: str | None = None
    artifact_uri: str | None = None
    note: str | None = None


class ContextSection(BaseModel):
    kind: ContextSectionKind
    title: str
    content: str
    structured: dict[str, Any] = Field(default_factory=dict)
    priority: int = 50
    required: bool = False
    estimated_tokens: int = 0
    source_refs: list[SourceRef] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.estimated_tokens <= 0:
            object.__setattr__(self, "estimated_tokens", estimate_tokens(self.title, self.content))


class ContextMessage(BaseModel):
    role: MessageRole
    content: str


class OutputContract(BaseModel):
    name: str
    description: str
    json_schema: dict[str, Any]
    required: bool = True
    validation_hint: str | None = None


class ContextPackage(BaseModel):
    id: str
    task_id: str
    schema_id: str
    role: ContextRole
    cycle_index: int
    prompt_version: str
    model_name: str | None = None
    sections: list[ContextSection] = Field(default_factory=list)
    messages: list[ContextMessage] = Field(default_factory=list)
    output_contract: OutputContract
    source_refs: dict[str, list[str]] = Field(default_factory=dict)
    estimated_tokens: int = 0
    token_budget: int
    artifact_uri: str | None = None
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextBuildRequest(BaseModel):
    task_id: str
    schema_id: str
    role: ContextRole
    campaign_id: str | None = None
    operation_ids: list[str] = Field(default_factory=list)
    model_name: str | None = None
    prompt_version: str | None = None
    token_budget: int | None = None
    debug: bool = False
    force_include_source_tables: list[str] = Field(default_factory=list)


def estimate_tokens(*parts: str) -> int:
    text = " ".join(part for part in parts if part)
    return max(1, len(text.split()))
