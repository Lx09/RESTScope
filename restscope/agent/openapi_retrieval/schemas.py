"""Public contracts for IR-backed OpenAPI investigation requests and results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..operation_test import OperationReference


class ParameterValueProducerQuery(BaseModel):
    """Find operations that may produce a value consumed by one operation."""

    model_config = ConfigDict(extra="forbid")

    objective: Literal["parameter_value_producer"]
    consumer_method: str
    consumer_path: str
    parameter_name: str
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("consumer_method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("consumer_method must not be blank")
        return normalized

    @field_validator("consumer_path")
    @classmethod
    def require_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith("/") or "://" in normalized:
            raise ValueError("consumer_path must be a URL path starting with '/'")
        return normalized

    @field_validator("parameter_name")
    @classmethod
    def require_parameter_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("parameter_name must not be blank")
        return normalized


class OpenAPIRetrievalRequest(BaseModel):
    """One investigation query against the App-bound OpenAPI IR."""

    model_config = ConfigDict(extra="forbid")

    query: ParameterValueProducerQuery


class TargetParameterMatch(BaseModel):
    location: Literal["path", "query", "header", "cookie", "body"]
    field_path: str = ""
    required: bool = False
    description: str | None = None


class TargetParameterSummary(BaseModel):
    name: str
    matches: list[TargetParameterMatch] = Field(default_factory=list)


class RetrievalEvidence(BaseModel):
    id: str
    operation: OperationReference | None = None
    kind: Literal[
        "symbol",
        "operation",
        "parameter",
        "response_field",
        "response_header",
        "link",
    ]
    location: str
    summary: str


class ParameterProducerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: OperationReference
    confidence: Literal["high", "medium", "low"]
    value_locations: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class EvidenceConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class InvestigationAction(BaseModel):
    tool: str
    summary: str
    result_count: int = 0


class InvestigationSummary(BaseModel):
    tool_calls: int = 0
    tool_result_bytes: int = 0
    elapsed_ms: int = 0
    actions: list[InvestigationAction] = Field(default_factory=list)
    evidence_sufficient: bool = False
    limitations: list[str] = Field(default_factory=list)


class OpenAPIRetrievalResult(BaseModel):
    objective: Literal["parameter_value_producer"]
    status: Literal["found", "not_found", "insufficient_evidence"]
    consumer_operation: OperationReference
    target_parameter: TargetParameterSummary
    candidates: list[ParameterProducerCandidate] = Field(default_factory=list)
    evidence: list[RetrievalEvidence] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    investigation_summary: InvestigationSummary
    warnings: list[str] = Field(default_factory=list)


class OpenAPIRetrievalDraft(BaseModel):
    """Model-produced conclusion before trusted context is attached."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["found", "not_found", "insufficient_evidence"]
    candidates: list[ParameterProducerCandidate] = Field(default_factory=list)
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    evidence_sufficient: bool
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
