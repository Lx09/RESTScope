"""Expose reference-only worklist reads and atomic whole-list replacement.

These tools are the complete model-facing Interface to mutable Resolution
state. Their schemas contain no Patch, Test Case, Memory, Generator,
Constraint, or persistence DTOs; precise objects remain in trusted registries.
"""

from __future__ import annotations

from functools import partial

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from restscope.tools import AgentToolbox, ToolBinding, ToolFailure
from restscope.llm import ToolSpec

from restscope.operation_smoke.failure_resolution.schemas import (
    FailureWorklist,
    WorklistItem,
    _WORKLIST_ITEM_ID_PATTERN,
)
from restscope.operation_smoke.failure_resolution.worklist import FailureWorklistStore


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
            "Patch, Test Case, Schema, Memory, Generator, or Constraint objects. "
            "For apply_patch, decision.selected_candidate_ref must name a P* also "
            "listed in that item's candidate_refs; for no_patch, omit it or use null."
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
    specs = {
        READ_WORKLIST_TOOL_NAME: read_worklist_tool_spec(),
        WRITE_WORKLIST_TOOL_NAME: write_worklist_tool_spec(),
    }
    for binding in worklist_tool_bindings(store):
        toolbox.register(spec=specs[binding.name], execute=binding.execute)


def worklist_tool_bindings(
    store: FailureWorklistStore,
) -> tuple[ToolBinding, ...]:
    """Bind read and atomic replacement Tools to one session-local Worklist."""
    return (
        ToolBinding(
            name=READ_WORKLIST_TOOL_NAME,
            execute=lambda: {
                "structured": store.read().model_dump(mode="json"),
            },
        ),
        ToolBinding(
            name=WRITE_WORKLIST_TOOL_NAME,
            execute=partial(_write_worklist, store=store),
        ),
    )


def _write_worklist(
    *,
    store: FailureWorklistStore,
    expected_revision: int,
    items: list[object],
    active_item_id: str | None = None,
) -> dict[str, object]:
    """Validate one model-authored replacement before changing the store.

    Args:
        store: Session-local worklist whose current revision may be replaced.
        expected_revision: Revision the Agent believes it is replacing.
        items: Complete untrusted item list generated in one tool call.
        active_item_id: Item the Agent wants to investigate next, if any.

    Returns:
        The successfully replaced worklist under the toolbox's structured
        result key.

    State changes and errors:
        Pydantic-only item relationships become safe ``ToolFailure`` feedback
        and leave the store unchanged. A valid request delegates the remaining
        revision and issued-reference checks to ``FailureWorklistStore``.
    """
    try:
        request = _WriteInput.model_validate(
            {
                "expected_revision": expected_revision,
                "active_item_id": active_item_id,
                "items": items,
            }
        )
    except ValidationError:
        # Standard JSON Schema cannot compare one selected P* value with the
        # contents of a sibling list. Treat that model mistake as correctable
        # input rather than recording it as an internal programming exception.
        raise ToolFailure(
            code="invalid_worklist_item",
            message=(
                "One or more Worklist items violate the decision or reference rules. "
                "For apply_patch, selected_candidate_ref must also appear in "
                "candidate_refs; use unique E*, TC*, and P* references with their "
                "issued formats."
            ),
        ) from None

    return {
        "structured": store.write(
            expected_revision=request.expected_revision,
            active_item_id=request.active_item_id,
            items=request.items,
        ).model_dump(mode="json"),
    }
