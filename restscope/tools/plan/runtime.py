"""Own the complete Interface and private state for a generic Agent Plan.

An authorized Agent reads or replaces a short list of task steps through two
Tools. The Harness creates one :class:`AgentPlanStore` for each Agent session,
so the model receives useful planning state without sharing hidden memory with
its parent, children, or siblings. The Module returns structured Plan values to
the Agent and never persists or publishes them to the Live Observer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from restscope.llm import ToolSpec
from restscope.tools.runtime import ToolBinding, ToolFailure


PLAN_READ_TOOL_NAME = "plan.read"
PLAN_UPDATE_TOOL_NAME = "plan.update"


def _add_single_active_step_schema(schema: dict[str, object]) -> None:
    """Express the one-active-step rule in the model-facing JSON Schema.

    Pydantic's model validator protects direct Python construction, but models
    send Tool arguments through JSON Schema first. ``minContains=0`` permits a
    Plan with no active step, while ``maxContains=1`` rejects two active steps
    before mutable session state is reached.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    plan_schema = properties.get("plan")
    if not isinstance(plan_schema, dict):
        return
    plan_schema.update(
        {
            "contains": {
                "type": "object",
                "properties": {"status": {"const": "in_progress"}},
                "required": ["status"],
            },
            "minContains": 0,
            "maxContains": 1,
        }
    )


def _add_update_plan_schema(schema: dict[str, object]) -> None:
    """Keep explanation optional while rejecting an explicit JSON null."""
    _add_single_active_step_schema(schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    explanation_schema = properties.get("explanation")
    if not isinstance(explanation_schema, dict):
        return
    alternatives = explanation_schema.get("anyOf")
    if not isinstance(alternatives, list):
        return
    string_schema = next(
        (
            alternative
            for alternative in alternatives
            if isinstance(alternative, dict) and alternative.get("type") == "string"
        ),
        None,
    )
    if string_schema is not None:
        properties["explanation"] = string_schema


class AgentPlanItem(BaseModel):
    """Describe one bounded task step and its current progress.

    Args:
        step: Plain-language work that the owning Agent can complete or revise.
        status: Whether the step is waiting, currently active, or finished.

    Items have no durable identity because the owning Agent replaces its whole
    private Plan and may freely reorder or rewrite steps as evidence changes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    step: str = Field(min_length=1, max_length=1_000)
    status: Literal["pending", "in_progress", "completed"]


class AgentPlan(BaseModel):
    """Return the complete current Plan for one Agent session.

    Args:
        explanation: Optional reason supplied with the latest replacement.
        plan: Zero to 100 ordered steps, with at most one active step.

    The value is an in-memory Tool result, not a scheduler queue, recovery
    checkpoint, cross-Agent message, or persistence record.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra=_add_single_active_step_schema,
    )

    explanation: str | None = Field(min_length=1, max_length=2_000)
    plan: tuple[AgentPlanItem, ...] = Field(max_length=100)

    @model_validator(mode="after")
    def require_at_most_one_active_step(self) -> "AgentPlan":
        """Reject ambiguous Plans that claim two steps are currently active."""
        if sum(item.status == "in_progress" for item in self.plan) > 1:
            raise ValueError("Agent Plan may contain at most one in_progress step")
        return self


class _ReadPlanInput(BaseModel):
    """Require a Plan read to carry no selector or hidden behavior switch."""

    model_config = ConfigDict(extra="forbid")


class _UpdatePlanInput(BaseModel):
    """Validate one complete model-authored replacement before mutation."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_add_update_plan_schema,
    )

    explanation: str | None = Field(default=None, min_length=1, max_length=2_000)
    plan: tuple[AgentPlanItem, ...] = Field(max_length=100)

    @field_validator("explanation", mode="before")
    @classmethod
    def reject_explicit_null_explanation(cls, value: object) -> object:
        """Distinguish an omitted explanation from a caller-supplied null."""
        if value is None:
            raise ValueError("Plan explanation must be omitted or contain text")
        return value

    @model_validator(mode="after")
    def require_at_most_one_active_step(self) -> "_UpdatePlanInput":
        """Apply the same active-step rule when a Binding is called directly."""
        if sum(item.status == "in_progress" for item in self.plan) > 1:
            raise ValueError("Agent Plan may contain at most one in_progress step")
        return self


class AgentPlanStore:
    """Keep one Agent session's complete private Plan in memory.

    The generic Agent executes one Tool call at a time and never shares this
    Store, so replacement needs neither a revision field nor a lock. Reads and
    writes return defensive copies to keep mutation behind this Interface.
    """

    def __init__(self) -> None:
        """Start one Agent session with an empty, unexplained Plan."""
        self._value = AgentPlan(explanation=None, plan=())

    def read(self) -> AgentPlan:
        """Return a defensive copy of the owning Agent's current Plan."""
        return self._value.model_copy(deep=True)

    def replace(self, value: AgentPlan) -> AgentPlan:
        """Replace the complete Plan and return a separate validated copy.

        Args:
            value: Already validated explanation and ordered step list.

        State changes:
            The previous Plan is discarded. Empty steps intentionally clear the
            Plan, and no transition rules restrict replanning or reopening work.
        """
        self._value = value.model_copy(deep=True)
        return self._value.model_copy(deep=True)


def plan_read_tool_spec() -> ToolSpec:
    """Return the global contract for reading one Agent's private Plan."""
    return ToolSpec(
        name=PLAN_READ_TOOL_NAME,
        description=(
            "Read this Agent session's complete private task Plan. The Plan is "
            "not shared with other Agents and is not a scheduler or checkpoint."
        ),
        kind="local_function",
        input_schema=_ReadPlanInput.model_json_schema(),
        output_schema=AgentPlan.model_json_schema(),
        strict=True,
    )


def plan_update_tool_spec() -> ToolSpec:
    """Return the global contract for replacing one Agent's private Plan."""
    return ToolSpec(
        name=PLAN_UPDATE_TOOL_NAME,
        description=(
            "Replace this Agent session's complete private task Plan. Supply "
            "zero to 100 ordered steps and at most one in_progress step."
        ),
        kind="local_function",
        input_schema=_UpdatePlanInput.model_json_schema(),
        output_schema=AgentPlan.model_json_schema(),
        strict=True,
    )


def plan_tool_bindings(store: AgentPlanStore) -> tuple[ToolBinding, ...]:
    """Bind both Plan Tools to exactly one Harness-created Agent Store."""

    def read() -> dict[str, object]:
        return {"structured": store.read().model_dump(mode="json")}

    def update(**arguments) -> dict[str, object]:
        try:
            replacement = _UpdatePlanInput.model_validate(arguments)
        except ValidationError:
            # JSON Schema expresses the active-step relationship, but direct
            # Binding callers still receive a safe, correctable failure.
            raise ToolFailure(
                code="invalid_agent_plan",
                message=(
                    "The Plan must contain zero to 100 bounded steps and at most "
                    "one in_progress step."
                ),
            ) from None
        value = AgentPlan(
            explanation=replacement.explanation,
            plan=replacement.plan,
        )
        return {"structured": store.replace(value).model_dump(mode="json")}

    return (
        ToolBinding(name=PLAN_READ_TOOL_NAME, execute=read),
        ToolBinding(name=PLAN_UPDATE_TOOL_NAME, execute=update),
    )
