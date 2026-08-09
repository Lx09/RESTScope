"""Protect Parameter Patch as a packaged standard Skill with lazy references."""

from __future__ import annotations

from importlib.resources import files
import json
from typing import Any

import pytest
import yaml


class _SkillProvider:
    """Return scripted model responses without contacting an external provider."""

    name = "scripted"

    def __init__(self, responses: list[Any]) -> None:
        """Keep ordered responses and every request for protocol assertions."""
        self.responses = list(responses)
        self.requests: list[Any] = []

    def invoke(self, request):
        """Record one request and return its predetermined response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _client(*responses):
    """Build the real LLM client around one local scripted provider."""
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry

    provider = _SkillProvider(list(responses))
    registry = LLMProviderRegistry()
    registry.register(provider)
    return LLMClient(registry), provider


def _model():
    """Return an enabled model configuration for the generic Harness Agent."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="fast",
        provider="scripted",
        model="fast-model",
        context_window_tokens=16_384,
        max_tokens=512,
    )


def _binding_factories(names: tuple[str, ...]):
    """Bind only domain Tools; the Harness owns Skill and file readers."""
    from restscope.harness import ToolBindingFactory
    from restscope.tools import ToolBinding
    from restscope.tools.file import FILE_READ_TOOL_NAME

    def factory(name: str) -> ToolBindingFactory:
        """Capture one exact Catalog name in its session factory."""
        return ToolBindingFactory(
            name=name,
            create=lambda: ToolBinding(name=name, execute=lambda **_arguments: {}),
        )

    return tuple(factory(name) for name in names if name != FILE_READ_TOOL_NAME)


def _parameter_patch_skill():
    """Resolve Parameter Patch through the same built-in Catalog as production."""
    from restscope.skills import builtin_skill_catalog

    return builtin_skill_catalog().get("parameter-patch")


def test_parameter_patch_skill_declares_its_complete_read_only_grants() -> None:
    """The standard files alone must drive the bounded runtime definition."""
    skill = _parameter_patch_skill()
    manifest = skill.manifest

    assert manifest.name == "parameter-patch"
    assert manifest.version == "1.0"
    assert manifest.risk_level == "low"
    assert manifest.required_tools == (
        "resource.list_resources",
        "resource.list_ids",
        "openapi.find_observed_response_fields",
        "file.read",
    )
    assert manifest.required_context_sources == ()
    assert manifest.instruction_artifact_uri is None
    assert len(skill.instructions) <= 24_000

    skill_root = files("restscope.builtin_skills").joinpath("parameter-patch")
    skill_source = skill_root.joinpath("SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill_source.split("---", 2)[1])
    runtime_manifest = yaml.safe_load(
        skill_root.joinpath("restscope.yaml").read_text(encoding="utf-8")
    )

    assert frontmatter == {
        "name": "parameter-patch",
        "description": manifest.description,
    }
    assert runtime_manifest == {
        "version": "1.0",
        "risk_level": "low",
        "required_tools": list(manifest.required_tools),
        "required_context_sources": [],
    }


def test_parameter_patch_core_and_references_remain_separate() -> None:
    """Skill loading must reveal the core body without flattening its library."""
    skill = _parameter_patch_skill()
    skill_root = files("restscope.builtin_skills").joinpath("parameter-patch")
    skill_source = skill_root.joinpath("SKILL.md").read_text(encoding="utf-8")
    expected_core = skill_source.split("---", 2)[2].strip()
    expected_paths = (
        "references/proposal-protocol.md",
        "references/generators.md",
        "references/constraints.md",
        "references/compiler-and-sampling.md",
        "references/review.md",
    )

    assert skill.instructions == expected_core
    assert tuple(reference.path for reference in skill.references) == expected_paths
    assert "# Generator construction and patching" not in skill.instructions
    for reference in skill.references:
        expected = skill_root.joinpath(reference.path).read_text(encoding="utf-8").strip()
        assert reference.content == expected
        assert 0 < len(reference.content) <= 24_000


@pytest.mark.parametrize(
    "missing_tool",
    (
        "resource.list_resources",
        "resource.list_ids",
        "openapi.find_observed_response_fields",
        "file.read",
    ),
)
def test_parameter_patch_rejects_a_profile_missing_any_required_tool(
    missing_tool: str,
) -> None:
    """Harness validation must fail before an incompletely authorized launch."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness

    skill = _parameter_patch_skill()
    granted = tuple(
        name for name in skill.manifest.required_tools if name != missing_tool
    )
    client, _provider = _client()

    with pytest.raises(
        ValueError,
        match=rf"parameter-patch requires Tool {missing_tool}",
    ):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="patch",
                        model_config_name="fast",
                        tool_names=granted,
                        skill_names=("parameter-patch",),
                    ),
                ),
                models=(_model(),),
                client=client,
                tool_binding_factories=_binding_factories(granted),
            )
        )


def test_skill_and_reference_bodies_are_each_loaded_only_on_demand() -> None:
    """Metadata, core instructions, and one Reference enter in separate stages."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMResponse, ToolCall

    skill = _parameter_patch_skill()
    reference = skill.reference("references/generators.md")
    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="fast-model",
            tool_calls=[
                ToolCall(
                    id="read-patch-skill",
                    name="skill.read",
                    arguments={"name": "parameter-patch"},
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="fast-model",
            tool_calls=[
                ToolCall(
                    id="read-generator-reference",
                    name="file.read",
                    arguments={
                        "skill_name": "parameter-patch",
                        "path": reference.path,
                    },
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="fast-model",
            parsed_json={"summary": "Patch method loaded.", "findings": []},
        ),
    )
    required_tools = skill.manifest.required_tools
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="patch",
                    model_config_name="fast",
                    tool_names=required_tools,
                    skill_names=("parameter-patch",),
                ),
            ),
            models=(_model(),),
            client=client,
            tool_binding_factories=_binding_factories(required_tools),
        )
    )

    result = runtime.start_main_agent("patch").run(
        AgentTask(objective="Prepare one bounded Parameter Patch.")
    )

    assert result.status == "completed"
    assert [tool.name for tool in provider.requests[0].tools] == [
        *required_tools,
        "skill.read",
    ]
    stable_context = provider.requests[0].messages[0].content
    assert "parameter-patch" in stable_context
    assert skill.manifest.description in stable_context
    assert skill.instructions not in stable_context
    assert reference.content not in stable_context

    second_request = "\n".join(
        message.content for message in provider.requests[1].messages
    )
    assert skill.instructions in second_request
    assert reference.content not in second_request

    third_request = "\n".join(
        message.content for message in provider.requests[2].messages
    )
    assert json.dumps(reference.content)[1:-1] in third_request
    assert skill.reference("references/proposal-protocol.md").content not in (
        third_request
    )
