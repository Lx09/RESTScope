"""Expose bounded read-only request-generation state and Patch validation.

The Tool Module owns both JSON contracts, expected failure translation, and
model-facing projection. ``RequestGenerationPatchRuntime`` remains the trusted domain
implementation; these Tools never send HTTP requests or mutate generation
state.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from restscope.llm import ToolSpec
from restscope.request_generation.parameter_patch import (
    RequestGenerationPatchRuntime,
    ParameterPatchValidationError,
    SemanticParameterPatch,
    validation_payload,
)
from restscope.request_generation.store import GeneratorConfigError
from restscope.tools.runtime import ToolBinding, ToolFailure


REQUEST_GENERATION_GET_INPUT_STATE_TOOL_NAME = "request_generation.get_input_state"
REQUEST_GENERATION_VALIDATE_PATCH_TOOL_NAME = "request_generation.validate_patch"

_SemanticRecord = Annotated[
    dict[str, object],
    Field(description="One bounded semantic record whose fields depend on its type."),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetInputStateInput(_StrictModel):
    """Select one operation and a bounded unique set of semantic inputs."""

    operation_key: str = Field(min_length=1, max_length=1_000)
    inputs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("inputs")
    @classmethod
    def require_unique_inputs(cls, value: list[str]) -> list[str]:
        """Reject duplicate handles before any Store lookup."""
        if len(value) != len(set(value)):
            raise ValueError("inputs must be unique")
        if any(not item or len(item) > 1_000 for item in value):
            raise ValueError("each input must contain 1 to 1000 characters")
        return value


class ValidatePatchInput(_StrictModel):
    """Carry one exact semantic Patch to deterministic validation."""

    operation_key: str = Field(min_length=1, max_length=1_000)
    expected_revision: int = Field(ge=0)
    affected_inputs: list[str] = Field(min_length=1, max_length=20)
    patch: SemanticParameterPatch
    seed: int = Field(default=0, ge=0, le=2**63 - 1)
    sample_count: int = Field(default=5, ge=1, le=20)

    @field_validator("affected_inputs")
    @classmethod
    def require_unique_inputs(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicated Patch ownership."""
        if len(value) != len(set(value)):
            raise ValueError("affected_inputs must be unique")
        return value


class InputStateOutput(_StrictModel):
    """Validate the complete semantic state projection returned to the model."""

    operation_key: str
    revision: int = Field(ge=0)
    state_digest: str
    last_applied_validation_digest: str | None
    inputs: list[_SemanticRecord] = Field(
        description="Complete current Generator state keyed by semantic input handle."
    )
    constraints: list[_SemanticRecord] = Field(
        description="Complete semantic Constraint records in the selected closure."
    )
    additional_constraint_inputs: list[str]


class ValidatePatchOutput(_StrictModel):
    """Validate compiled state facts and deterministic sample witnesses."""

    operation_key: str
    revision: int = Field(ge=0)
    state_digest: str
    validation_digest: str
    affected_inputs: list[str]
    final_generators: list[_SemanticRecord] = Field(
        description="Complete final Generator states for the affected scope."
    )
    domain_analysis: list[_SemanticRecord] = Field(
        description="Bounded semantic domain facts for each affected Generator."
    )
    final_constraints: list[_SemanticRecord] = Field(
        description="Complete final active Constraint records after replacement."
    )
    constraint_participants: list[str]
    samples: list[_SemanticRecord] = Field(
        min_length=1,
        max_length=20,
        description="Deterministic presence and value witnesses for review.",
    )


class RequestGenerationToolBackend:
    """Bind Store reads and deterministic validation to Tool-shaped methods."""

    def __init__(self, runtime: RequestGenerationPatchRuntime) -> None:
        self.runtime = runtime

    def get_input_state(self, **arguments: object) -> dict[str, object]:
        """Read current state without truncating its Constraint closure."""
        try:
            request = GetInputStateInput.model_validate(arguments)
            payload = self.runtime.read_state(
                operation_key=request.operation_key,
                input_handles=request.inputs,
            )
            output = InputStateOutput.model_validate(payload)
        except ValidationError as exc:
            raise ToolFailure(
                code="invalid_request_generation_state_request",
                message="Input-state arguments are invalid",
            ) from exc
        except (GeneratorConfigError, ParameterPatchValidationError) as exc:
            raise ToolFailure(
                code=getattr(exc, "code", "request_generation_state_invalid"),
                message=str(exc),
            ) from exc
        return {"structured": output.model_dump(mode="json")}

    def validate_patch(self, **arguments: object) -> dict[str, object]:
        """Compile and sample a Patch without changing App or target state."""
        try:
            request = ValidatePatchInput.model_validate(arguments)
            validated = self.runtime.validate(
                operation_key=request.operation_key,
                expected_revision=request.expected_revision,
                affected_inputs=request.affected_inputs,
                patch=request.patch,
                seed=request.seed,
                sample_count=request.sample_count,
            )
            output = ValidatePatchOutput.model_validate(validation_payload(validated))
        except ValidationError as exc:
            raise ToolFailure(
                code="invalid_parameter_patch",
                message="Parameter Patch does not match the semantic contract",
            ) from exc
        except (GeneratorConfigError, ParameterPatchValidationError, ValueError) as exc:
            raise ToolFailure(
                code=getattr(exc, "code", "parameter_patch_validation_failed"),
                message=str(exc),
            ) from exc
        return {"structured": output.model_dump(mode="json")}


def request_generation_get_input_state_tool_spec() -> ToolSpec:
    """Return the contract for reading exact Generator/Constraint state."""
    return ToolSpec(
        name=REQUEST_GENERATION_GET_INPUT_STATE_TOOL_NAME,
        description=(
            "Read complete current Generators and the direct/transitive active "
            "Constraint closure for 1 to 20 semantic inputs."
        ),
        kind="local_function",
        input_schema=GetInputStateInput.model_json_schema(),
        output_schema=InputStateOutput.model_json_schema(),
        strict=True,
    )


def request_generation_validate_patch_tool_spec() -> ToolSpec:
    """Return the contract for deterministic Patch compilation and witnesses."""
    return ToolSpec(
        name=REQUEST_GENERATION_VALIDATE_PATCH_TOOL_NAME,
        description=(
            "Compile a complete semantic Parameter Patch against an expected "
            "generation revision and return final domains, Constraints, and "
            "deterministic sample witnesses without applying it."
        ),
        kind="local_function",
        input_schema=ValidatePatchInput.model_json_schema(),
        output_schema=ValidatePatchOutput.model_json_schema(),
        strict=True,
    )


def request_generation_tool_bindings(
    backend: RequestGenerationToolBackend,
) -> tuple[ToolBinding, ...]:
    """Bind both read-only request-generation Tool implementations."""
    return (
        ToolBinding(
            name=REQUEST_GENERATION_GET_INPUT_STATE_TOOL_NAME,
            execute=backend.get_input_state,
        ),
        ToolBinding(
            name=REQUEST_GENERATION_VALIDATE_PATCH_TOOL_NAME,
            execute=backend.validate_patch,
        ),
    )
