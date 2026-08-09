"""Protect the in-memory, Profile-scoped Skill Reference reader."""

from __future__ import annotations

import pytest


def _skill(name: str, *references: tuple[str, str]):
    """Build one immutable Skill fixture with the supplied Reference library."""
    from restscope.skills import SkillDefinition, SkillManifest, SkillReference

    return SkillDefinition(
        manifest=SkillManifest(name=name, description=f"{name} description."),
        instructions="# Core",
        references=tuple(
            SkillReference(path=path, content=content)
            for path, content in references
        ),
    )


def test_file_read_returns_one_authorized_reference_without_duplication() -> None:
    """The visible content carries Markdown while structured data stays small."""
    from restscope.tools.file import file_read_tool_binding

    binding = file_read_tool_binding(
        (_skill("selected", ("references/method.md", "# Method\n\nUse it.")),)
    )

    result = binding.execute(
        skill_name="selected",
        path="references/method.md",
    )

    assert result == {
        "content": "# Method\n\nUse it.",
        "structured": {
            "skill_name": "selected",
            "path": "references/method.md",
            "characters": 17,
        },
    }


def test_file_read_distinguishes_unselected_skills_and_unknown_references() -> None:
    """Authorization failure must not reveal another Skill's Reference names."""
    from restscope.tools import ToolFailure
    from restscope.tools.file import file_read_tool_binding

    binding = file_read_tool_binding(
        (_skill("selected", ("references/method.md", "# Method")),)
    )

    with pytest.raises(ToolFailure) as unauthorized:
        binding.execute(skill_name="other", path="references/method.md")
    assert unauthorized.value.code == "skill_file_not_authorized"

    with pytest.raises(ToolFailure) as missing:
        binding.execute(skill_name="selected", path="references/unknown.md")
    assert missing.value.code == "skill_file_not_found"


@pytest.mark.parametrize(
    "path",
    (
        "../secret.md",
        "/etc/passwd",
        "SKILL.md",
        "references/deep/method.md",
        "references/code.py",
    ),
)
def test_file_read_schema_rejects_every_path_outside_one_level_markdown(
    path: str,
) -> None:
    """Local schema validation blocks path tricks before the Binding executes."""
    from restscope.tools import AgentToolbox, ToolCatalog, ToolDefinition
    from restscope.tools.file import file_read_tool_binding, file_read_tool_spec

    called = False
    authorized = file_read_tool_binding(
        (_skill("selected", ("references/method.md", "# Method")),)
    )

    def execute(**arguments):
        nonlocal called
        called = True
        return authorized.execute(**arguments)

    from restscope.tools import ToolBinding

    toolbox = AgentToolbox.from_catalog(
        catalog=ToolCatalog(
            [ToolDefinition(subject="file", spec=file_read_tool_spec())]
        ),
        selected_names=("file.read",),
        bindings=[ToolBinding(name="file.read", execute=execute)],
    )

    from restscope.llm import ToolCall

    result = toolbox.execute(
        ToolCall(
            id="read-invalid-path",
            name="file.read",
            arguments={"skill_name": "selected", "path": path},
        )
    )

    assert result.status == "denied"
    assert called is False
