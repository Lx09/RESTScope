"""Protect the project-native Parameter Patch Skill and its Harness grants."""

from __future__ import annotations

from typing import Any

import pytest


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
    """Provide inert bindings because these tests exercise Skill loading only."""
    from restscope.harness import ToolBindingFactory
    from restscope.tools import ToolBinding

    def factory(name: str) -> ToolBindingFactory:
        """Capture one exact Catalog name in its session factory."""
        return ToolBindingFactory(
            name=name,
            create=lambda: ToolBinding(name=name, execute=lambda **_arguments: {}),
        )

    return tuple(factory(name) for name in names)


def test_parameter_patch_skill_declares_its_complete_read_only_grants() -> None:
    """The reusable method must remain bounded and require no hidden access."""
    from restscope.skills import (
        PARAMETER_PATCH_PROPOSAL_INSTRUCTIONS,
        PARAMETER_PATCH_SKILL,
    )

    manifest = PARAMETER_PATCH_SKILL.manifest

    assert manifest.name == "parameter-patch"
    assert manifest.version == "1.0"
    assert manifest.risk_level == "low"
    assert manifest.required_tools == (
        "resource.list_resources",
        "resource.list_ids",
        "openapi.find_observed_response_fields",
    )
    assert manifest.required_context_sources == ()
    assert manifest.instruction_artifact_uri is None
    assert len(PARAMETER_PATCH_PROPOSAL_INSTRUCTIONS) <= 7_000
    assert len(PARAMETER_PATCH_SKILL.instructions) <= 24_000
    assert PARAMETER_PATCH_SKILL.instructions.startswith(
        PARAMETER_PATCH_PROPOSAL_INSTRUCTIONS
    )
    assert "## Self-review a compiled candidate" not in (
        PARAMETER_PATCH_PROPOSAL_INSTRUCTIONS
    )
    for required_heading in (
        "# Build a Parameter Patch",
        "## Evidence authority",
        "## Choose a Generator",
        "## Express cross-input Constraints",
        "## Correct a rejected proposal",
        "## Self-review a compiled candidate",
    ):
        assert required_heading in PARAMETER_PATCH_SKILL.instructions


def test_parameter_patch_skill_rejects_a_profile_missing_one_required_tool() -> None:
    """Harness validation must fail before the model sees an incomplete Profile."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.skills import PARAMETER_PATCH_SKILL

    granted = PARAMETER_PATCH_SKILL.manifest.required_tools[:-1]
    client, _provider = _client()

    with pytest.raises(
        ValueError,
        match="parameter-patch requires Tool openapi.find_observed_response_fields",
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
                skills=(PARAMETER_PATCH_SKILL,),
                tool_binding_factories=_binding_factories(granted),
            )
        )


def test_parameter_patch_skill_body_is_injected_only_after_skill_read() -> None:
    """Metadata is stable context while the detailed method remains on demand."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.skills import PARAMETER_PATCH_SKILL

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
            parsed_json={"summary": "Patch method loaded.", "findings": []},
        ),
    )
    required_tools = PARAMETER_PATCH_SKILL.manifest.required_tools
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
            skills=(PARAMETER_PATCH_SKILL,),
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
    assert PARAMETER_PATCH_SKILL.manifest.description in stable_context
    assert "# Build a Parameter Patch" not in stable_context
    injected_messages = [
        message.content
        for message in provider.requests[1].messages
        if "AUTHORIZED SKILL INSTRUCTIONS: parameter-patch" in message.content
    ]
    assert len(injected_messages) == 1
    assert PARAMETER_PATCH_SKILL.instructions in injected_messages[0]
