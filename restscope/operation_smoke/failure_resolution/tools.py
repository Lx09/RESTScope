"""Expose reference-only worklist reads and atomic whole-list replacement.

These tools are the complete model-facing Interface to mutable Resolution
state. Their schemas contain no Patch, Test Case, Memory, Generator,
Constraint, or persistence DTOs; precise objects remain in trusted registries.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from restscope.capabilities import AgentToolbox
from restscope.llm import ToolSpec

from .schemas import FailureWorklist, WorklistItem, _WORKLIST_ITEM_ID_PATTERN
from .worklist import FailureWorklistStore


READ_WORKLIST_TOOL_NAME = "failure_resolution.read_worklist"
WRITE_WORKLIST_TOOL_NAME = "failure_resolution.write_worklist"


class _ReadInput(BaseModel):
    """Require the read tool to receive no hidden selectors or actions."""

    model_config = ConfigDict(extra="forbid")


class _WriteInput(BaseModel):
    """Describe one optimistic, complete worklist replacement."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    active_item_id: str | None = Field(
        default=None,
        min_length=6,
        max_length=120,
        pattern=_WORKLIST_ITEM_ID_PATTERN,
    )
    items: list[WorklistItem]


def read_worklist_tool_spec() -> ToolSpec:
    """Describe retrieval of the current reference-only Agent state."""
    return ToolSpec(
        name=READ_WORKLIST_TOOL_NAME,
        description=(
            "Read the complete current Failure Resolution worklist. Precise "
            "Test Cases and Patch candidates remain behind their E*, TC*, and "
            "P* references."
        ),
        kind="local_function",
        input_schema=_ReadInput.model_json_schema(),
        output_schema=FailureWorklist.model_json_schema(),
        strict=True,
    )


def write_worklist_tool_spec() -> ToolSpec:
    """Describe one atomic replacement without semantic merge/split actions."""
    return ToolSpec(
        name=WRITE_WORKLIST_TOOL_NAME,
        description=(
            "Replace the entire Failure Resolution worklist at the expected "
            "revision. Use references and short semantic text only; never embed "
            "Patch, Test Case, Schema, Memory, Generator, or Constraint objects."
        ),
        kind="local_function",
        input_schema=_WriteInput.model_json_schema(),
        output_schema=FailureWorklist.model_json_schema(),
        strict=True,
    )


def register_worklist_tools(
    *,
    toolbox: AgentToolbox,
    store: FailureWorklistStore,
) -> None:
    """Register the only two model-visible operations over mutable worklist state."""
    toolbox.register(
        spec=read_worklist_tool_spec(),
        execute=lambda: {
            "structured": store.read().model_dump(mode="json"),
        },
    )
    toolbox.register(
        spec=write_worklist_tool_spec(),
        execute=lambda **arguments: {
            "structured": store.write(
                expected_revision=arguments["expected_revision"],
                active_item_id=arguments.get("active_item_id"),
                items=[
                    WorklistItem.model_validate(item)
                    for item in arguments["items"]
                ],
            ).model_dump(mode="json"),
        },
    )
