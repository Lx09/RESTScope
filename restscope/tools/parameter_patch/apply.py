"""Own the only model-callable mutation of request-generation Patch state.

``parameter_patch.apply`` accepts the exact semantic content already validated
by ``request_generation.validate_patch``. It revalidates under the operation
write lock, registers response-value sources transactionally, and atomically
replaces the in-memory Generator/Constraint revision. It never sends a target
HTTP request or records a Patch candidate/history entry.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from restscope.llm import ToolSpec
from restscope.request_generation.parameter_patch import (
    RequestGenerationPatchRuntime,
    ParameterPatchValidationError,
    SemanticParameterPatch,
)
from restscope.request_generation.store import GeneratorConfigError
from restscope.tools.runtime import ToolBinding, ToolFailure


PARAMETER_PATCH_APPLY_TOOL_NAME = "parameter_patch.apply"

_ReferenceBinding = Annotated[
    dict[str, object],
    Field(description="One bounded canonical resource or response-value source summary."),
]


class ApplyParameterPatchInput(BaseModel):
    """Carry the exact validation identity and semantic replacement."""

    model_config = ConfigDict(extra="forbid")

    operation_key: str = Field(min_length=1, max_length=1_000)
    expected_revision: int = Field(ge=0)
    validation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    affected_inputs: list[str] = Field(min_length=1, max_length=20)
    patch: SemanticParameterPatch
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    sample_count: int = Field(default=5, ge=1, le=20)

    @field_validator("affected_inputs")
    @classmethod
    def require_unique_inputs(cls, value: list[str]) -> list[str]:
        """Reject duplicate semantic input ownership before mutation."""
        if len(value) != len(set(value)):
            raise ValueError("affected_inputs must be unique")
        return value


class ApplyParameterPatchOutput(BaseModel):
    """Expose only the stable facts needed to confirm one successful Apply."""

    model_config = ConfigDict(extra="forbid")

    operation_key: str
    previous_revision: int = Field(ge=0)
    current_revision: int = Field(ge=1)
    validation_digest: str
    state_digest: str
    affected_inputs: list[str]
    generator_change_count: int = Field(ge=0)
    constraint_change_count: int = Field(ge=0)
    final_reference_bindings: list[_ReferenceBinding] = Field(
        description="Exact canonical-resource or response-producer bindings in the applied state."
    )
    removed_response_value_inputs: list[str]


class ParameterPatchApplyBackend:
    """Translate the mutating Tool call to the trusted Patch runtime."""

    def __init__(self, runtime: RequestGenerationPatchRuntime) -> None:
        self.runtime = runtime

    def apply(self, **arguments: object) -> dict[str, object]:
        """Revalidate and apply one exact Patch or return a correctable error."""
        try:
            request = ApplyParameterPatchInput.model_validate(arguments)
            result = self.runtime.apply(
                operation_key=request.operation_key,
                expected_revision=request.expected_revision,
                validation_digest=request.validation_digest,
                affected_inputs=request.affected_inputs,
                patch=request.patch,
                seed=request.seed,
                sample_count=request.sample_count,
            )
            applied = result.state
            validated = result.validated
            output = ApplyParameterPatchOutput(
                operation_key=applied.config.operation_key,
                previous_revision=result.previous_revision,
                current_revision=applied.revision,
                validation_digest=validated.validation_digest,
                state_digest=applied.state_digest,
                affected_inputs=list(validated.affected_inputs),
                generator_change_count=result.generator_change_count,
                constraint_change_count=result.constraint_change_count,
                final_reference_bindings=[
                    {
                        key: value
                        for key, value in {
                            "input": item.input,
                            "kind": item.kind,
                            "canonical_resource": item.canonical_resource,
                            "producer_operation_key": item.producer_operation_key,
                            "producer_status_code": item.producer_status_code,
                            "producer_media_type": item.producer_media_type,
                            "source_field": item.source_field,
                        }.items()
                        if value is not None
                    }
                    for item in result.final_reference_bindings
                ],
                removed_response_value_inputs=list(
                    result.removed_response_value_inputs
                ),
            )
        except ValidationError as exc:
            raise ToolFailure(
                code="invalid_parameter_patch_apply",
                message="Patch Apply arguments do not match the validated contract",
            ) from exc
        except (GeneratorConfigError, ParameterPatchValidationError, ValueError) as exc:
            raise ToolFailure(
                code=getattr(exc, "code", "parameter_patch_apply_failed"),
                message=str(exc),
            ) from exc
        return {"structured": output.model_dump(mode="json")}


def parameter_patch_apply_tool_spec() -> ToolSpec:
    """Return the single mutating Parameter Patch Tool contract."""
    return ToolSpec(
        name=PARAMETER_PATCH_APPLY_TOOL_NAME,
        description=(
            "Atomically apply the exact semantic Patch, revision, seed, sample "
            "count, and validation digest returned by validation. This changes "
            "future request generation only and sends no HTTP request."
        ),
        kind="local_function",
        input_schema=ApplyParameterPatchInput.model_json_schema(),
        output_schema=ApplyParameterPatchOutput.model_json_schema(),
        strict=True,
    )


def parameter_patch_apply_tool_binding(
    backend: ParameterPatchApplyBackend,
) -> ToolBinding:
    """Bind the one authorized Patch mutation to its App-lifetime runtime."""
    return ToolBinding(name=PARAMETER_PATCH_APPLY_TOOL_NAME, execute=backend.apply)
