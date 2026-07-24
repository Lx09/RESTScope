"""Internal contracts for two-round Operation Smoke diagnosis."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from restscope.testing import InputGeneratorPatch, OperationExecutionReport


class ParameterSuspect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_node_id: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("input_node_id", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text cannot be blank")
        return normalized

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("evidence references cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("evidence references cannot repeat")
        return normalized


class ParameterDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    no_parameter_issue: StrictBool
    suspects: list[ParameterSuspect] = Field(max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> "ParameterDiagnosis":
        if self.no_parameter_issue and self.suspects:
            raise ValueError("no_parameter_issue=true requires no suspects")
        if not self.no_parameter_issue and not self.suspects:
            raise ValueError("no_parameter_issue=false requires suspects")
        return self


class GeneratorPatchDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[InputGeneratorPatch] = Field(min_length=1, max_length=100)


class TwoRoundDiagnosisResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis: ParameterDiagnosis
    updates: list[InputGeneratorPatch] = Field(default_factory=list)


class OperationSmokeRequest(BaseModel):
    """One bounded smoke attempt for a persisted operation generator config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    max_feedback_rounds: int = Field(default=3, ge=0, le=10)
    seed: int | None = Field(default=None, ge=0)
    successful_operation_keys: list[str] = Field(default_factory=list)


class WaitingReference(BaseModel):
    """One reference-backed input whose persistent value pool is empty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str
    type: str
    name: str


class OperationSmokeResult(BaseModel):
    """Audit-friendly outcome produced without replaying an individual case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["passed", "failed", "waiting", "errored"]
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    active_config_revision: int = Field(ge=1)
    batch_reports: list[OperationExecutionReport] = Field(default_factory=list)
    diagnoses: list[TwoRoundDiagnosisResult] = Field(default_factory=list)
    waiting_references: list[WaitingReference] = Field(default_factory=list)
    error: dict[str, str] | None = None
