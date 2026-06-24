"""Pydantic schemas for the Memory layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryKind = Literal[
    "working",
    "operation",
    "observation",
    "constraint",
    "campaign",
    "testing_knowledge",
    "episodic",
]

MemoryRole = Literal[
    "planner",
    "result_analyst",
    "decision_maker",
    "check_designer",
    "intelligence_updater",
]


class MemoryItem(BaseModel):
    """Unified memory abstraction built from DB records."""

    id: str
    kind: MemoryKind
    schema_id: str | None = None
    task_id: str | None = None
    operation_id: str | None = None
    campaign_id: str | None = None
    observation_id: str | None = None
    title: str
    content: str
    structured: dict[str, Any] = Field(default_factory=dict)
    importance: float = 0.5
    confidence: float = 0.5
    recency_score: float = 0.5
    relevance_score: float = 0.5
    risk_score: float = 0.0
    source_table: str
    source_id: str
    estimated_tokens: int = 0

    def model_post_init(self, __context: Any) -> None:
        if self.estimated_tokens <= 0:
            object.__setattr__(self, "estimated_tokens", estimate_tokens(self.title, self.content))


class MemoryQuery(BaseModel):
    """Role-specific memory retrieval query."""

    schema_id: str
    task_id: str | None = None
    campaign_id: str | None = None
    role: MemoryRole
    operation_ids: list[str] = Field(default_factory=list)
    focus_keywords: list[str] = Field(default_factory=list)
    include_kinds: list[MemoryKind] = Field(default_factory=list)
    max_items: int = 40
    token_budget: int = 6000


class MemoryPackage(BaseModel):
    """Selected memory ready for ContextBuilder consumption."""

    schema_id: str
    task_id: str | None
    role: MemoryRole
    working_memory: list[MemoryItem] = Field(default_factory=list)
    operation_memory: list[MemoryItem] = Field(default_factory=list)
    observation_memory: list[MemoryItem] = Field(default_factory=list)
    constraint_memory: list[MemoryItem] = Field(default_factory=list)
    campaign_memory: list[MemoryItem] = Field(default_factory=list)
    testing_knowledge_memory: list[MemoryItem] = Field(default_factory=list)
    episodic_memory: list[MemoryItem] = Field(default_factory=list)
    selected_operation_ids: list[str] = Field(default_factory=list)
    source_refs: dict[str, list[str]] = Field(default_factory=dict)
    estimated_tokens: int = 0

    @classmethod
    def from_items(
        cls,
        *,
        schema_id: str,
        task_id: str | None,
        role: MemoryRole,
        items: list[MemoryItem],
        selected_operation_ids: list[str] | None = None,
    ) -> "MemoryPackage":
        grouped: dict[str, list[MemoryItem]] = {
            "working": [],
            "operation": [],
            "observation": [],
            "constraint": [],
            "campaign": [],
            "testing_knowledge": [],
            "episodic": [],
        }
        source_refs: dict[str, list[str]] = {}
        for item in items:
            grouped[item.kind].append(item)
            source_refs.setdefault(item.source_table, [])
            if item.source_id not in source_refs[item.source_table]:
                source_refs[item.source_table].append(item.source_id)
        return cls(
            schema_id=schema_id,
            task_id=task_id,
            role=role,
            working_memory=grouped["working"],
            operation_memory=grouped["operation"],
            observation_memory=grouped["observation"],
            constraint_memory=grouped["constraint"],
            campaign_memory=grouped["campaign"],
            testing_knowledge_memory=grouped["testing_knowledge"],
            episodic_memory=grouped["episodic"],
            selected_operation_ids=selected_operation_ids or [],
            source_refs=source_refs,
            estimated_tokens=sum(item.estimated_tokens for item in items),
        )


def estimate_tokens(*parts: str) -> int:
    text = " ".join(part for part in parts if part)
    return max(1, len(text.split()))
