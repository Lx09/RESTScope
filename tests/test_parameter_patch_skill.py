"""Protect Apply Parameter Patch as a packaged, progressively disclosed Skill."""

from __future__ import annotations

from importlib.resources import files

import pytest
import yaml


SKILL_NAME = "apply-parameter-patch"


def test_apply_parameter_patch_manifest_and_references_are_exact() -> None:
    """The standard files are the sole authority for the Patch workflow."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(SKILL_NAME)
    expected_tools = (
        "file.read",
        "resource.list_resources",
        "resource.list_ids",
        "openapi.find_observed_response_fields",
        "request_generation.get_input_state",
        "request_generation.validate_patch",
        "parameter_patch.apply",
    )
    expected_references = (
        "references/proposal-protocol.md",
        "references/generators.md",
        "references/constraints.md",
        "references/compiler-and-sampling.md",
        "references/review.md",
        "references/application.md",
    )

    assert skill.manifest.name == SKILL_NAME
    assert skill.manifest.version == "1.0"
    assert skill.manifest.risk_level == "medium"
    assert skill.manifest.required_tools == expected_tools
    assert skill.manifest.required_context_sources == ()
    assert tuple(reference.path for reference in skill.references) == expected_references
    assert len(skill.instructions) <= 24_000
    assert all(0 < len(reference.content) <= 24_000 for reference in skill.references)

    root = files("restscope.builtin_skills").joinpath(SKILL_NAME)
    source = root.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(source.split("---", 2)[1])
    manifest = yaml.safe_load(root.joinpath("restscope.yaml").read_text(encoding="utf-8"))
    assert frontmatter == {
        "name": SKILL_NAME,
        "description": skill.manifest.description,
    }
    assert manifest == {
        "version": "1.0",
        "risk_level": "medium",
        "required_tools": list(expected_tools),
        "required_context_sources": [],
    }


def test_patch_skill_requires_state_validate_review_apply_and_confirmation() -> None:
    """The method must forbid applying a merely compiled or sampled proposal."""
    from restscope.skills import builtin_skill_catalog

    skill = builtin_skill_catalog().get(SKILL_NAME)
    combined = "\n".join(
        [skill.instructions, *(item.content for item in skill.references)]
    )
    ordered_terms = (
        "request_generation.get_input_state",
        "request_generation.validate_patch",
        "parameter_patch.apply",
    )
    positions = [skill.instructions.index(term) for term in ordered_terms]
    assert positions == sorted(positions)
    assert "value predicate" in combined.lower()
    assert "state conflict" in combined.lower()
    assert "complete replacement" in combined.lower()
    assert "commit fails" in combined.lower()
    assert "reference binding" in combined.lower()
    assert "HTTP success" in combined
    assert "prove" in combined.lower()
    assert "build-parameter-patch" not in combined


@pytest.mark.parametrize(
    "missing_tool",
    (
        "file.read",
        "resource.list_resources",
        "resource.list_ids",
        "openapi.find_observed_response_fields",
        "request_generation.get_input_state",
        "request_generation.validate_patch",
        "parameter_patch.apply",
    ),
)
def test_profile_missing_any_patch_dependency_is_rejected(missing_tool: str) -> None:
    """Harness validation fails before an incompletely authorized launch."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.skills import builtin_skill_catalog

    required = builtin_skill_catalog().get(SKILL_NAME).manifest.required_tools
    granted = tuple(name for name in required if name != missing_tool)
    class Provider:
        """Exist only so Profile validation reaches Skill dependencies."""

        name = "unused"

    registry = LLMProviderRegistry()
    registry.register(Provider())
    with pytest.raises(ValueError, match=f"{SKILL_NAME} requires Tool {missing_tool}"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="patch",
                        model_config_name="fast",
                        tool_names=granted,
                        skill_names=(SKILL_NAME,),
                    ),
                ),
                models=(
                    LLMModelConfig(
                        name="fast",
                        provider="unused",
                        model="unused",
                        max_tokens=256,
                        context_window_tokens=8_192,
                    ),
                ),
                client=LLMClient(registry),
            )
        )


def test_old_patch_skill_names_are_absent() -> None:
    """The rename is a clean public break without compatibility aliases."""
    from restscope.skills import builtin_skill_catalog

    catalog = builtin_skill_catalog()
    assert catalog.get(SKILL_NAME).name == SKILL_NAME
    for retired in ("parameter-patch", "build-parameter-patch"):
        with pytest.raises(KeyError):
            catalog.get(retired)
