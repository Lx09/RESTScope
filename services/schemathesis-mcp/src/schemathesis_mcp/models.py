"""Pydantic models for MCP run requests, statuses, events, and results."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class RunState(str, Enum):
    QUEUED = "queued"
    LOADING = "loading"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RunOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"
    INTERRUPTED = "interrupted"


class RunProgress(BaseModel):
    events: int = 0
    scenarios: int = 0
    failures: int = 0
    errors: int = 0
    status_code_counts: dict[str, int] = Field(default_factory=dict)


class RunStatus(BaseModel):
    run_id: str
    state: RunState = RunState.QUEUED
    outcome: RunOutcome | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    current_phase: str | None = None
    stop_reason: str | None = None
    progress: RunProgress = Field(default_factory=RunProgress)
    error: str | None = None


class EventEntry(BaseModel):
    cursor: int
    payload: dict[str, Any]


class EventPage(BaseModel):
    events: list[EventEntry]
    next_cursor: int
    artifact_uri: str


class FileSchema(BaseModel):
    kind: Literal["file"]
    path: str


class UrlSchema(BaseModel):
    kind: Literal["url"]
    url: str


class InlineSchema(BaseModel):
    kind: Literal["inline"]
    format: Literal["yaml", "json"] = "yaml"
    content: str


SchemaInput = Annotated[FileSchema | UrlSchema | InlineSchema, Field(discriminator="kind")]


class RunRequest(BaseModel):
    schema_input: SchemaInput = Field(alias="schema")
    base_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    phases: list[str] | None = None
    checks: list[str] | None = None
    generation_modes: list[str] | None = None
    include: dict[str, Any] = Field(default_factory=dict)
    exclude: dict[str, Any] = Field(default_factory=dict)
    workers: int | str | None = None
    max_examples: int | None = None
    max_failures: int | None = None
    max_time: float | None = None
    seed: int | None = None
    tls_verify: bool = True
    reports: list[Literal["junit", "har", "vcr", "allure"]] = Field(default_factory=list)


class FailureDetail(BaseModel):
    failure_id: str
    operation: str
    check: str
    title: str
    message: str
    count: int = 1
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    curl: str | None = None
    case: dict[str, Any] | None = None
    related_cases: list[dict[str, Any]] = Field(default_factory=list)


class RunResult(BaseModel):
    run_id: str
    outcome: RunOutcome
    stop_reason: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    failure_ids: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    cli_version: str | None = None
    command: str | None = None
    exit_code: int | None = None
    schema_info: dict[str, Any] | None = Field(default=None, alias="schema")
