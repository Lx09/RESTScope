"""Expose one Profile-scoped in-memory reader for Skill Reference Markdown.

The standard Skill loader has already decoded, bounded, and registered every
Reference before this Tool is bound. Calls select from that immutable mapping;
they never translate model input into a filesystem path and therefore cannot
read source code, configuration, credentials, assets, scripts, or other Skills.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from restscope.llm import ToolSpec
from restscope.skills import SkillDefinition
from restscope.tools.runtime import ToolBinding, ToolFailure


FILE_READ_TOOL_NAME = "file.read"
_REFERENCE_PATH_PATTERN = r"^references/[A-Za-z0-9][A-Za-z0-9._-]*\.md$"


class _ReadSkillFileInput(BaseModel):
    """Select one registered Reference from one Profile-selected Skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str = Field(min_length=1, max_length=120)
    path: str = Field(
        min_length=15,
        max_length=1_000,
        pattern=_REFERENCE_PATH_PATTERN,
    )


class _ReadSkillFileOutput(BaseModel):
    """Describe returned Markdown without repeating it in structured output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str = Field(min_length=1, max_length=120)
    path: str = Field(
        min_length=15,
        max_length=1_000,
        pattern=_REFERENCE_PATH_PATTERN,
    )
    characters: int = Field(ge=1, le=24_000)


def file_read_tool_spec() -> ToolSpec:
    """Return the closed contract for reading one authorized Skill Reference."""
    return ToolSpec(
        name=FILE_READ_TOOL_NAME,
        description=(
            "Read one Markdown Reference directly linked by a Skill selected "
            "by this Agent Profile. Only paths shaped like "
            "references/<filename>.md are available."
        ),
        kind="local_function",
        input_schema=_ReadSkillFileInput.model_json_schema(),
        output_schema=_ReadSkillFileOutput.model_json_schema(),
        strict=True,
    )


def file_read_tool_binding(skills: Iterable[SkillDefinition]) -> ToolBinding:
    """Bind an in-memory Reference reader to exactly one Profile's Skills.

    Args:
        skills: Definitions already selected and policy-checked by the Harness.

    Returns:
        A Binding that returns the complete requested Markdown once and only
        small identity metadata in its structured result.
    """
    authorized = {
        skill.name: {reference.path: reference for reference in skill.references}
        for skill in skills
    }

    def read(*, skill_name: str, path: str) -> dict[str, object]:
        # A distinct authorization error avoids revealing whether another
        # installed but unselected Skill happens to contain the requested path.
        if skill_name not in authorized:
            raise ToolFailure(
                code="skill_file_not_authorized",
                message="The requested Skill is not selected by this Agent Profile.",
            )
        try:
            reference = authorized[skill_name][path]
        except KeyError as exc:
            raise ToolFailure(
                code="skill_file_not_found",
                message="The requested Reference is not registered for this Skill.",
            ) from exc
        return {
            "content": reference.content,
            "structured": _ReadSkillFileOutput(
                skill_name=skill_name,
                path=path,
                characters=len(reference.content),
            ).model_dump(mode="json"),
        }

    return ToolBinding(name=FILE_READ_TOOL_NAME, execute=read)
