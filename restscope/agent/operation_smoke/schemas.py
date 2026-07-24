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


class AvailableReferenceOption(BaseModel):
    """One system-verified, non-empty reference pool exposed without values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(min_length=1, max_length=100)
    input_node_id: str = Field(min_length=1, max_length=1000)
    kind: Literal["resource_identifier", "response_value"]
    canonical_resource: str | None = Field(default=None, max_length=200)
    value_name: str | None = Field(default=None, max_length=200)
    compatible_scalar_type: str | None = Field(default=None, max_length=50)
    value_count: int = Field(ge=1)
    producer_operation_keys: list[str] = Field(default_factory=list, max_length=100)
    producer_status_code: str | None = Field(default=None, max_length=20)
    producer_media_type: str | None = Field(default=None, max_length=200)
    source_field: str | None = Field(default=None, max_length=1000)
    source_selector: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "AvailableReferenceOption":
        if self.kind == "resource_identifier":
            if not self.canonical_resource or self.value_name is not None:
                raise ValueError(
                    "resource_identifier requires canonical_resource only"
                )
        elif (
            not self.value_name
            or self.canonical_resource is not None
            or len(self.producer_operation_keys) != 1
            or not self.producer_status_code
            or not self.producer_media_type
            or not self.source_field
            or not self.source_selector
        ):
            raise ValueError(
                "response_value requires one complete producer source"
            )
        return self


class ReferenceGeneratorSelection(BaseModel):
    """Model selection of a system-provided reference option."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_node_id: str = Field(min_length=1, max_length=1000)
    reference_option_id: str = Field(min_length=1, max_length=100)
    inclusion_probability: float | None = Field(default=None, ge=0, le=1)


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

    updates: list[InputGeneratorPatch] = Field(default_factory=list, max_length=100)
    reference_selections: list[ReferenceGeneratorSelection] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def require_update(self) -> "GeneratorPatchDraft":
        if not self.updates and not self.reference_selections:
            raise ValueError("at least one generator update is required")
        if len(self.updates) + len(self.reference_selections) > 100:
            raise ValueError("at most 100 generator updates are allowed")
        return self


class TwoRoundDiagnosisResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis: ParameterDiagnosis
    updates: list[InputGeneratorPatch] = Field(default_factory=list)
    selected_reference_options: list[AvailableReferenceOption] = Field(
        default_factory=list
    )


class OperationSmokeRequest(BaseModel):
    """One bounded smoke attempt for a persisted operation generator config."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation_key: str = Field(min_length=1)
    case_count: int = Field(default=10, ge=1, le=20)
    success_rate_threshold: float = Field(default=0.8, ge=0, le=1)
    max_feedback_rounds: int = Field(default=3, ge=0, le=10)
    seed: int | None = Field(default=None, ge=0)


class OperationSmokeResult(BaseModel):
    """Audit-friendly outcome produced without replaying an individual case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["passed", "retry", "unsupported", "errored"]
    operation_key: str
    success_rate: float = Field(ge=0, le=1)
    required_success_rate: float = Field(ge=0, le=1)
    active_config_revision: int = Field(ge=1)
    batch_reports: list[OperationExecutionReport] = Field(default_factory=list)
    diagnoses: list[TwoRoundDiagnosisResult] = Field(default_factory=list)
    failure_kind: Literal[
        "threshold_exhausted",
        "no_parameter_issue",
        "unsupported_operation",
        "operation_error",
    ] | None = None
    error: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_failure_kind(self) -> "OperationSmokeResult":
        allowed = {
            "passed": {None},
            "retry": {"threshold_exhausted", "no_parameter_issue"},
            "unsupported": {"unsupported_operation"},
            "errored": {"operation_error"},
        }
        if self.failure_kind not in allowed[self.status]:
            raise ValueError(
                f"{self.status} has incompatible failure_kind "
                f"{self.failure_kind!r}"
            )
        return self
