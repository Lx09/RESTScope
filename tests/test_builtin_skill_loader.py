"""Exercise strict loading of packaged standard Skill directories."""

from __future__ import annotations

from pathlib import Path

import pytest


def _write_skill(
    root: Path,
    *,
    directory_name: str = "example",
    name: str = "example",
    body: str = "# Example\n\nFollow this method.",
    runtime: str | None = None,
    references: dict[str, str] | None = None,
) -> Path:
    """Create one tiny standard Skill fixture below a temporary package root."""
    skill_dir = root / directory_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: A small test Skill.\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    if runtime is not None:
        (skill_dir / "restscope.yaml").write_text(runtime, encoding="utf-8")
    for relative_path, content in (references or {}).items():
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


def test_discovery_is_sorted_and_runtime_manifest_is_strict(tmp_path: Path) -> None:
    """Package traversal is deterministic and rejects unknown extension keys."""
    from restscope.skills.loader import discover_skill_definitions

    _write_skill(tmp_path, directory_name="zeta", name="zeta")
    _write_skill(tmp_path, directory_name="alpha", name="alpha")

    assert tuple(skill.name for skill in discover_skill_definitions(tmp_path)) == (
        "alpha",
        "zeta",
    )

    invalid_root = tmp_path / "invalid"
    _write_skill(
        invalid_root,
        runtime="version: '1.0'\nrisk_level: low\nunknown: true\n",
    )
    with pytest.raises(ValueError, match="restscope.yaml"):
        discover_skill_definitions(invalid_root)

    incomplete_root = tmp_path / "incomplete"
    _write_skill(incomplete_root, runtime="version: '1.0'\nrisk_level: low\n")
    with pytest.raises(ValueError, match="restscope.yaml"):
        discover_skill_definitions(incomplete_root)


@pytest.mark.parametrize(
    ("frontmatter", "message"),
    (
        (
            "---\nname: wrong\ndescription: Desc.\n---\n\n# Body\n",
            "directory name",
        ),
        (
            "---\nname: example\ndescription: Desc.\nversion: '1'\n---\n\n# Body\n",
            "frontmatter",
        ),
        ("---\nname: example\n---\n\n# Body\n", "frontmatter"),
    ),
)
def test_frontmatter_is_standard_and_directory_name_must_match(
    tmp_path: Path,
    frontmatter: str,
    message: str,
) -> None:
    """RESTScope metadata cannot leak into standard SKILL.md frontmatter."""
    from restscope.skills.loader import discover_skill_definitions

    skill_dir = tmp_path / "example"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        discover_skill_definitions(tmp_path)


def test_references_are_linked_one_level_markdown_and_require_file_read(
    tmp_path: Path,
) -> None:
    """A Skill cannot silently package or expose unregistered Reference files."""
    from restscope.skills.loader import discover_skill_definitions

    body = "# Example\n\nRead [method](references/method.md)."
    _write_skill(
        tmp_path,
        body=body,
        references={"references/method.md": "# Method\n\nDetails."},
    )
    with pytest.raises(ValueError, match="file.read"):
        discover_skill_definitions(tmp_path)

    runtime = (
        "version: '1.0'\n"
        "risk_level: low\n"
        "required_tools: [file.read]\n"
        "required_context_sources: []\n"
    )
    (tmp_path / "example" / "restscope.yaml").write_text(runtime, encoding="utf-8")
    (tmp_path / "example" / "references" / "orphan.md").write_text(
        "# Orphan", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not linked"):
        discover_skill_definitions(tmp_path)

    (tmp_path / "example" / "references" / "orphan.md").unlink()
    skill = discover_skill_definitions(tmp_path)[0]
    assert skill.reference("references/method.md").content == "# Method\n\nDetails."


@pytest.mark.parametrize(
    ("body", "references", "message"),
    (
        (
            "# Example\n\n[missing](references/missing.md)",
            {},
            "missing",
        ),
        (
            "# Example\n\n[a](references/a.md) and [again](references/a.md)",
            {"references/a.md": "# A"},
            "more than once",
        ),
        (
            "# Example\n\n[escape](references/../secret.md)",
            {},
            "Reference link",
        ),
        (
            "# Example\n\n[nested](references/deep/a.md)",
            {"references/deep/a.md": "# Nested"},
            "Reference link",
        ),
        (
            "# Example\n\n[blank](references/blank.md)",
            {"references/blank.md": "   \n"},
            "blank",
        ),
        (
            "# Example\n\n[large](references/large.md)",
            {"references/large.md": "x" * 24_001},
            "24000",
        ),
    ),
)
def test_invalid_reference_libraries_fail_closed(
    tmp_path: Path,
    body: str,
    references: dict[str, str],
    message: str,
) -> None:
    """Malformed libraries fail during discovery, before an Agent can start."""
    from restscope.skills.loader import discover_skill_definitions

    runtime = (
        "version: '1.0'\n"
        "risk_level: low\n"
        "required_tools: [file.read]\n"
        "required_context_sources: []\n"
    )
    _write_skill(tmp_path, body=body, runtime=runtime, references=references)

    with pytest.raises(ValueError, match=message):
        discover_skill_definitions(tmp_path)


def test_non_utf8_reference_and_duplicate_dependencies_fail_closed(
    tmp_path: Path,
) -> None:
    """Unreadable content and ambiguous access declarations are rejected."""
    from restscope.skills.loader import discover_skill_definitions

    runtime = (
        "version: '1.0'\n"
        "risk_level: low\n"
        "required_tools: [file.read, file.read]\n"
        "required_context_sources: []\n"
    )
    skill_dir = _write_skill(
        tmp_path,
        body="# Example\n\n[bad](references/bad.md)",
        runtime=runtime,
        references={"references/bad.md": "placeholder"},
    )
    with pytest.raises(ValueError, match="unique"):
        discover_skill_definitions(tmp_path)

    (skill_dir / "restscope.yaml").write_text(
        runtime.replace("[file.read, file.read]", "[file.read]"),
        encoding="utf-8",
    )
    (skill_dir / "references" / "bad.md").write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        discover_skill_definitions(tmp_path)


def test_additional_definitions_cannot_replace_a_builtin() -> None:
    """Caller-supplied test Skills augment built-ins but cannot override them."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.skills import SkillDefinition, SkillManifest

    duplicate = SkillDefinition(
        manifest=SkillManifest(
            name="apply-parameter-patch",
            description="Attempted replacement.",
        ),
        instructions="Do something else.",
    )
    client, _provider = _client_for_duplicate_test()

    with pytest.raises(ValueError, match="Skill is duplicated: apply-parameter-patch"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(name="main", model_config_name="fast"),
                ),
                models=(_model_for_duplicate_test(),),
                client=client,
                skills=(duplicate,),
            )
        )


def _client_for_duplicate_test():
    """Build the smallest provider registry used by duplicate validation."""
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry

    class Provider:
        """Provide a registered name; duplicate validation runs before invoke."""

        name = "scripted"

        def invoke(self, _request):  # pragma: no cover - validation fails first.
            raise AssertionError("provider should not run")

    registry = LLMProviderRegistry()
    registry.register(Provider())
    return LLMClient(registry), registry


def _model_for_duplicate_test():
    """Return one enabled model for the duplicate Skill Harness fixture."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        name="fast",
        provider="scripted",
        model="fast-model",
        context_window_tokens=8_192,
        max_tokens=256,
    )
