"""Define model contracts for Parameter history and Patch construction Tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.llm import ToolSpec
from .contracts import PatchCandidateSummary
from restscope.tools.runtime import ToolBinding


PARAMETER_HISTORY_TOOL_NAME = "lookup_parameter_history"
GENERATE_PARAMETER_PATCH_TOOL_NAME = "generate_parameter_patch"

_GeneratorDiff = Annotated[
    dict[str, Any],
    Field(
        description=(
            "One Generator diff with fields determined by the Generator type; "
            "internal runtime identities have been removed."
        )
    ),
]
_ConstraintDiff = Annotated[
    dict[str, Any],
    Field(
        description=(
            "One Constraint diff with fields determined by the Constraint type; "
            "internal runtime identities have been removed."
        )
    ),
]


class _ParameterToolOutput(BaseModel):
    """Reject undocumented fields in Parameter Tool success results."""

    model_config = ConfigDict(extra="forbid")


class _AttributedParameter(_ParameterToolOutput):
    """Name one semantic input associated with a prior conclusion."""

    cause_summary: str = Field(
        min_length=1,
        description="Why the prior conclusion attributed this input.",
    )
    input_handle: str = Field(
        min_length=1,
        description="Semantic input handle; persistence identities are removed.",
    )


class _AcceptedChange(_ParameterToolOutput):
    """Describe the bounded deterministic diff from an accepted Patch."""

    event_id: str = Field(min_length=1, description="Audit event reference.")
    reason: str = Field(min_length=1, description="Recorded reason for the change.")
    generator_changes: list[_GeneratorDiff] = Field(
        default_factory=list,
        description=(
            "Generator diffs after internal identities are removed. Each object "
            "is intentionally open because its fields depend on Generator type."
        ),
    )
    constraint_changes: list[_ConstraintDiff] = Field(
        default_factory=list,
        description=(
            "Constraint diffs after internal identities are removed. Each object "
            "is intentionally open because its fields depend on Constraint type."
        ),
    )


class _PriorAttempt(_ParameterToolOutput):
    """Expose one terminal Resolution conclusion without storage identities."""

    round_number: int = Field(ge=1)
    outcome: Literal["applied_patch", "no_patch", "conflict"]
    reason: str = Field(min_length=1)
    root_cause: str | None = None
    parameters: list[_AttributedParameter] = Field(default_factory=list)
    generator_change: _AcceptedChange | None = None


class _PriorFailure(_ParameterToolOutput):
    """Group chronological conclusions for one stable prior Failure."""

    summary: str = Field(min_length=1)
    occurrence_count: int = Field(ge=1)
    attempts: list[_PriorAttempt] = Field(default_factory=list)


class _ParameterHistory(_ParameterToolOutput):
    """Return all prior Failure evidence attributed to one semantic input."""

    input_handle: str = Field(min_length=1)
    failures: list[_PriorFailure] = Field(default_factory=list)


class _ParameterHistoryResult(_ParameterToolOutput):
    """Wrap the single requested Parameter in the Tool result envelope."""

    parameters: list[_ParameterHistory] = Field(min_length=1, max_length=1)


def parameter_history_tool_binding(
    execute: Callable[..., dict[str, Any]],
) -> ToolBinding:
    """Bind the Parameter history Tool to one bounded Memory reader."""
    return ToolBinding(name=PARAMETER_HISTORY_TOOL_NAME, execute=execute)


def generate_parameter_patch_tool_binding(
    execute: Callable[..., dict[str, Any]],
) -> ToolBinding:
    """Bind candidate construction to the owning Resolution session Adapter."""
    return ToolBinding(name=GENERATE_PARAMETER_PATCH_TOOL_NAME, execute=execute)


def parameter_history_tool_spec() -> ToolSpec:
    """Describe a one-Parameter history read without preloading Memory."""
    return ToolSpec(
        name=PARAMETER_HISTORY_TOOL_NAME,
        description=(
            "Read prior attributed Failures, root causes, conflicts, and applied "
            "changes for exactly one semantic Parameter handle discovered from "
            "OpenAPI or Test Case evidence."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "input_handles": {
                    "type": "array",
                    "description": "Exactly one semantic Parameter handle.",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["input_handles"],
            "additionalProperties": False,
        },
        output_schema=_ParameterHistoryResult.model_json_schema(),
        strict=True,
    )


def generate_parameter_patch_tool_spec() -> ToolSpec:
    """Describe side-effect-free construction of one reviewed Patch candidate."""
    return ToolSpec(
        name=GENERATE_PARAMETER_PATCH_TOOL_NAME,
        description=(
            "Build, compile, sample, and independently review one Generator or "
            "Constraint candidate for the active worklist item. Returns only a "
            "new P* reference and bounded summary; it does not apply the Patch."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "root_cause": {
                    "type": "string",
                    "description": "Current evidence-based root-cause statement.",
                    "minLength": 1,
                    "maxLength": 1_200,
                },
                "affected_inputs": {
                    "type": "array",
                    "description": "Unique semantic Parameter handles to change.",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 100,
                    "uniqueItems": True,
                },
                "value_requirements": {
                    "type": "string",
                    "description": "Values or relationships the candidate must produce.",
                    "minLength": 1,
                    "maxLength": 4_000,
                },
                "acceptance_criteria": {
                    "type": "array",
                    "description": "Unique checks the reviewed candidate must satisfy.",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 20,
                    "uniqueItems": True,
                },
            },
            "required": [
                "root_cause",
                "affected_inputs",
                "value_requirements",
                "acceptance_criteria",
            ],
            "additionalProperties": False,
        },
        output_schema=PatchCandidateSummary.model_json_schema(),
        strict=True,
    )
