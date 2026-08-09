"""Expose one validated Patch candidate through a bounded read Tool.

The Tool Module owns the model contract and Markdown projection. The Harness
injects a session-local candidate Registry, whose precise executable objects
remain unavailable to the model.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from restscope.context import CompactTextWriter
from restscope.llm import ToolSpec
from restscope.tools.runtime import AgentToolbox, ToolBinding
from .contracts import PatchCandidateSummary


class CandidateSummaryBackend(Protocol):
    """Read one issued candidate without exposing the registry implementation."""

    def summary(self, candidate_ref: str) -> PatchCandidateSummary:
        """Return the bounded summary for an exact session-local reference."""


READ_CANDIDATE_TOOL_NAME = "parameter_patch.read_candidate"


class _ReadCandidateInput(BaseModel):
    """Accept exactly one candidate reference and no executable fields."""

    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(
        pattern=r"^P[1-9][0-9]*$",
        description="Session-local reference of one reviewed Patch candidate.",
    )


def read_candidate_tool_spec() -> ToolSpec:
    """Describe bounded recovery of one issued candidate's semantic details."""
    return ToolSpec(
        name=READ_CANDIDATE_TOOL_NAME,
        description=(
            "Read a validated Patch candidate summary by P* reference. The "
            "result never includes executable Patch DTOs or generated values."
        ),
        kind="local_function",
        input_schema=_ReadCandidateInput.model_json_schema(),
        output_schema=PatchCandidateSummary.model_json_schema(),
        strict=True,
    )


def register_candidate_read_tool(
    *,
    toolbox: AgentToolbox,
    registry: CandidateSummaryBackend,
) -> None:
    """Bind the candidate read Tool to one Resolution session Registry."""

    binding = candidate_read_tool_binding(registry)
    toolbox.register(spec=read_candidate_tool_spec(), execute=binding.execute)


def candidate_read_tool_binding(
    registry: CandidateSummaryBackend,
) -> ToolBinding:
    """Bind the bounded candidate projection to one session Registry."""

    def read(candidate_ref: str) -> dict[str, Any]:
        """Return bounded Markdown and the strict non-executable summary DTO."""
        summary = registry.summary(candidate_ref)
        writer = CompactTextWriter(max_value_chars=1_200)
        writer.section(
            f"VALIDATED PATCH CANDIDATE {summary.candidate_ref}",
            untrusted=True,
        )
        writer.record(
            "candidate",
            root_cause=summary.root_cause,
            affected_parameters=summary.affected_parameters,
            generator_changes=summary.generator_change_overview,
            constraint_changes=summary.constraint_change_overview,
            sample_count=summary.sample_overview.sample_count,
            sample_coverage=summary.sample_overview.covered_parameters,
        )
        return {
            "content": writer.render(max_chars=8_000).text,
            "structured": summary.model_dump(mode="json"),
        }

    return ToolBinding(name=READ_CANDIDATE_TOOL_NAME, execute=read)
