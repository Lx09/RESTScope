"""Own the contract and authorization Adapter for on-demand Skill loading.

An Agent Profile selects reusable Skills by name. The Harness binds this one
Tool to exactly those resolved definitions and returns only an acknowledgement;
the owning private Prompt Session adds the already-loaded instruction text to
the conversation after the legal assistant/tool protocol group is recorded.
The Tool executes no Skill code and owns no persistent state.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from restscope.llm import ToolSpec
from restscope.skills import SkillDefinition
from restscope.tools.runtime import ToolBinding, ToolFailure

SKILL_READ_TOOL_NAME = "skill.read"


class _ReadSkillInput(BaseModel):
    """Select one Skill name from the current Profile's authorized set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)


class _ReadSkillOutput(BaseModel):
    """Acknowledge that the Prompt Session may now add the instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    status: Literal["instructions_added"]


def skill_read_tool_spec() -> ToolSpec:
    """Return the global closed contract for reading one selected Skill."""
    return ToolSpec(
        name=SKILL_READ_TOOL_NAME,
        description=(
            "Load the core SKILL.md instructions for one Skill selected by this "
            "Agent Profile. The instructions are added to the next user message; "
            "linked References require an authorized file.read call."
        ),
        kind="local_function",
        input_schema=_ReadSkillInput.model_json_schema(),
        output_schema=_ReadSkillOutput.model_json_schema(),
        strict=True,
    )


def skill_read_tool_binding(skills: Iterable[SkillDefinition]) -> ToolBinding:
    """Bind the loader to exactly one Agent's ordered authorized Skill set.

    Args:
        skills: Definitions already resolved and policy-checked by the Harness.

    Returns:
        One immutable Binding. Unknown or unselected names become a safe Tool
        failure instead of exposing Catalog contents or instruction text.
    """
    authorized = {skill.name: skill for skill in skills}

    def read(name: str) -> dict[str, object]:
        if name not in authorized:
            raise ToolFailure(
                code="skill_not_authorized",
                message=(
                    "The requested Skill is not selected by this Agent Profile."
                ),
            )
        return {
            "structured": _ReadSkillOutput(
                name=name,
                status="instructions_added",
            ).model_dump(mode="json")
        }

    return ToolBinding(name=SKILL_READ_TOOL_NAME, execute=read)
