"""Protect generic Profile prompt behavior through the public Harness seam."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest


class _PromptProvider:
    """Return a fixed response sequence and retain every complete request."""

    name = "scripted"

    def __init__(self, responses: list[Any]) -> None:
        """Keep deterministic responses so tests never call a real model."""
        self.responses = list(responses)
        self.requests: list[Any] = []

    def invoke(self, request):
        """Record the request before returning the next scripted response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _client(*responses):
    """Build the real client around one local recording provider."""
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry

    provider = _PromptProvider(list(responses))
    registry = LLMProviderRegistry()
    registry.register(provider)
    return LLMClient(registry), provider


def _model(
    *,
    role: str = "thinking",
    model: str = "thinking-model",
    context_window_tokens: int = 8_192,
    max_tokens: int = 512,
):
    """Return one enabled model with an adjustable prompt window."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role=role,
        provider="scripted",
        model=model,
        context_window_tokens=context_window_tokens,
        max_tokens=max_tokens,
    )


def _completion(summary: str = "Done"):
    """Return one valid generic Agent completion."""
    from restscope.llm import LLMResponse

    return LLMResponse(
        provider="scripted",
        model="thinking-model",
        parsed_json={"summary": summary, "findings": []},
    )


def test_skill_read_is_auto_appended_and_injects_only_authorized_instructions() -> None:
    """Skill metadata is stable; each successful read adds one user instruction."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.skills import SkillDefinition, SkillManifest

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(id="skill-1", name="skill.read", arguments={"name": "inspect"})
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(id="skill-2", name="skill.read", arguments={"name": "inspect"})
            ],
        ),
        _completion(),
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    skill_names=("inspect",),
                ),
            ),
            models=(_model(),),
            client=client,
            skills=(
                SkillDefinition(
                    manifest=SkillManifest(
                        name="inspect",
                        description="Inspect evidence in a bounded sequence.",
                        version="1.2",
                    ),
                    instructions="FIRST inspect the authorized evidence; THEN summarize.",
                ),
            ),
        )
    )

    result = runtime.start_main_agent("main").run(
        AgentTask(objective="Inspect the current evidence.")
    )

    assert result.status == "completed"
    assert [tool.name for tool in provider.requests[0].tools] == ["skill.read"]
    system = provider.requests[0].messages[0].content
    assert "inspect" in system
    assert "Inspect evidence in a bounded sequence." in system
    assert "version=1.2" in system
    assert "FIRST inspect" not in system
    assert "additionalProperties" not in "\n".join(
        message.content for message in provider.requests[0].messages
    )
    injected = [
        message.content
        for request in provider.requests[1:]
        for message in request.messages
        if "AUTHORIZED SKILL INSTRUCTIONS: inspect" in message.content
    ]
    assert len(injected) == 3
    assert all("FIRST inspect" in content for content in injected)
    assert provider.requests[1].messages[-1].role == "user"
    assert provider.requests[1].messages[-2].role == "tool"


def test_skill_loader_denies_unselected_names_and_cannot_be_overridden() -> None:
    """The Harness owns the loader and selected Skill names are its only grants."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import ToolCall
    from restscope.skills import SkillDefinition, SkillManifest
    from restscope.tools import ToolBinding

    client, _provider = _client()
    definition = AgentRuntimeDefinition(
        profiles=(
            AgentProfile(
                name="main",
                model_config_name="thinking",
                skill_names=("inspect",),
            ),
        ),
        models=(_model(),),
        client=client,
        skills=(
            SkillDefinition(
                manifest=SkillManifest(name="inspect", description="Inspect."),
                instructions="Inspect safely.",
            ),
        ),
    )
    agent = build_harness(agent_runtime=definition).start_main_agent("main")

    allowed = agent.toolbox.execute(
        ToolCall(id="good", name="skill.read", arguments={"name": "inspect"})
    )
    denied = agent.toolbox.execute(
        ToolCall(id="bad", name="skill.read", arguments={"name": "other"})
    )
    invalid = agent.toolbox.execute(
        ToolCall(
            id="extra",
            name="skill.read",
            arguments={"name": "inspect", "unexpected": True},
        )
    )

    assert allowed.status == "succeeded"
    assert allowed.structured == {
        "name": "inspect",
        "status": "instructions_added",
    }
    assert denied.status == "failed"
    assert denied.error["code"] == "skill_not_authorized"
    assert invalid.status == "denied"
    assert invalid.error["code"] == "invalid_tool_arguments"
    with pytest.raises(ValueError, match="Skill Tool Binding is owned by Harness"):
        build_harness(
            agent_runtime=replace(
                definition,
                tool_binding_factories=(
                    ToolBindingFactory(
                        name="skill.read",
                        create=lambda: ToolBinding(
                            name="skill.read",
                            execute=lambda name: {"name": name},
                        ),
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="must not declare skill.read"):
        build_harness(
            agent_runtime=replace(
                definition,
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="thinking",
                        tool_names=("skill.read",),
                        skill_names=("inspect",),
                    ),
                ),
            )
        )


def test_context_sources_are_incremental_and_empty_changes_are_explicit() -> None:
    """One Main session sends full first state and only later replacements."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ContextSourceBinding, build_harness

    current = {"value": "## Operation\nGET /pets"}
    client, provider = _client(
        _completion("one"),
        _completion("two"),
        _completion("three"),
        _completion("four"),
    )
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    context_sources=("operation",),
                ),
            ),
            models=(_model(),),
            client=client,
            context_sources=(
                ContextSourceBinding(name="operation", read=lambda: current["value"]),
            ),
        )
    ).start_main_agent("main")

    agent.run(AgentTask(objective="First task"))
    agent.run(AgentTask(objective="Second task"))
    current["value"] = "## Operation\nPOST /pets"
    agent.run(AgentTask(objective="Third task"))
    current["value"] = ""
    agent.run(AgentTask(objective="Fourth task"))

    first_task = next(
        message.content
        for message in provider.requests[0].messages
        if "First task" in message.content
    )
    second_task = next(
        message.content
        for message in provider.requests[1].messages
        if "Second task" in message.content
    )
    third_task = next(
        message.content
        for message in provider.requests[2].messages
        if "Third task" in message.content
    )
    fourth_task = next(
        message.content
        for message in provider.requests[3].messages
        if "Fourth task" in message.content
    )
    assert "AUTHORIZED CONTEXT: operation" in first_task
    assert "## Operation\nGET /pets" in first_task
    assert "AUTHORIZED CONTEXT: operation" not in second_task
    assert "AUTHORIZED CONTEXT: operation" in third_task
    assert "POST /pets" in third_task
    assert "AUTHORIZED CONTEXT: operation" in fourth_task
    assert "CONTEXT SOURCE IS EMPTY" in fourth_task


def test_parent_prompt_lists_only_direct_children_in_developer_role() -> None:
    """Child discovery is stable developer guidance, not inherited child state."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness

    lifecycle_tools = ("subagent.start", "subagent.wait", "subagent.cancel")
    client, provider = _client(_completion())
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=lifecycle_tools,
                    subagent_profile_names=("research",),
                ),
                AgentProfile(
                    name="research",
                    description="Research one bounded primary-source question.",
                    model_config_name="thinking",
                    tool_names=lifecycle_tools,
                    subagent_profile_names=("specialist",),
                ),
                AgentProfile(
                    name="specialist",
                    description="Inspect one specialist detail.",
                    model_config_name="thinking",
                ),
            ),
            models=(_model(),),
            client=client,
        )
    ).start_main_agent("main")

    agent.run(AgentTask(objective="Delegate if useful."))

    messages = provider.requests[0].messages
    assert [message.role for message in messages[:3]] == ["system", "developer", "user"]
    assert "research" in messages[1].content
    assert "Research one bounded" in messages[1].content
    assert "specialist" not in messages[1].content


def test_child_uses_its_own_profile_prompt_and_not_parent_history() -> None:
    """A child starts a fresh Prompt Session with only its selected access."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import (
        AgentRuntimeDefinition,
        ContextSourceBinding,
        build_harness,
    )
    from restscope.llm import ToolCall
    from restscope.skills import SkillDefinition, SkillManifest

    lifecycle_tools = ("subagent.start", "subagent.wait", "subagent.cancel")
    client, provider = _client(_completion("parent"), _completion("child"))
    main = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=lifecycle_tools,
                    skill_names=("parent-skill",),
                    context_sources=("parent-context",),
                    subagent_profile_names=("child",),
                ),
                AgentProfile(
                    name="child",
                    description="Inspect one isolated child question.",
                    model_config_name="fast",
                    skill_names=("child-skill",),
                    context_sources=("child-context",),
                ),
            ),
            models=(
                _model(),
                _model(role="fast", model="fast-model"),
            ),
            client=client,
            skills=(
                SkillDefinition(
                    manifest=SkillManifest(
                        name="parent-skill",
                        description="Parent-only method.",
                        required_context_sources=("parent-context",),
                    ),
                    instructions="Parent-only body.",
                ),
                SkillDefinition(
                    manifest=SkillManifest(
                        name="child-skill",
                        description="Child-only method.",
                        required_context_sources=("child-context",),
                    ),
                    instructions="Child-only body.",
                ),
            ),
            context_sources=(
                ContextSourceBinding(name="parent-context", read=lambda: "PARENT-EVIDENCE"),
                ContextSourceBinding(name="child-context", read=lambda: "CHILD-EVIDENCE"),
            ),
        )
    ).start_main_agent("main")
    main.run(AgentTask(objective="PARENT-OBJECTIVE"))

    started = main.toolbox.execute(
        ToolCall(
            id="start-child",
            name="subagent.start",
            arguments={"profile_name": "child", "objective": "CHILD-OBJECTIVE"},
        )
    )
    child_id = started.structured["subagent_id"]
    while True:
        waited = main.toolbox.execute(
            ToolCall(
                id="wait-child",
                name="subagent.wait",
                arguments={"subagent_ids": [child_id], "timeout_seconds": 5},
            )
        )
        if waited.structured["agents"][0]["status"] != "running":
            break

    child_request = provider.requests[1]
    child_text = "\n".join(message.content for message in child_request.messages)
    assert child_request.metadata["role"] == "child"
    assert provider.requests[0].model == "thinking-model"
    assert child_request.model == "fast-model"
    assert [tool.name for tool in child_request.tools] == ["skill.read"]
    assert "child-skill" in child_request.messages[0].content
    assert "parent-skill" not in child_request.messages[0].content
    assert "CHILD-OBJECTIVE" in child_text
    assert "CHILD-EVIDENCE" in child_text
    assert "PARENT-OBJECTIVE" not in child_text
    assert "PARENT-EVIDENCE" not in child_text


def test_protocol_reservation_can_stop_before_model_or_tool_execution() -> None:
    """Immutable output schema size is reserved instead of clipping instructions."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness

    client, provider = _client()
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(AgentProfile(name="main", model_config_name="thinking"),),
            models=(_model(context_window_tokens=300, max_tokens=128),),
            client=client,
        )
    ).start_main_agent("main")

    result = agent.run(AgentTask(objective="This must not reach the model."))

    assert result.status == "context_budget_exceeded"
    assert result.error.code == "context_budget_exceeded"
    assert provider.requests == []


def test_oversized_context_source_stops_before_model_use() -> None:
    """The Harness validates bounded Markdown instead of silently truncating it."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ContextSourceBinding, build_harness

    client, provider = _client()
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    context_sources=("operation",),
                ),
            ),
            models=(_model(),),
            client=client,
            context_sources=(
                ContextSourceBinding(
                    name="operation",
                    read=lambda: "X" * 101,
                    max_chars=100,
                ),
            ),
        )
    ).start_main_agent("main")

    result = agent.run(AgentTask(objective="Inspect."))

    assert result.status == "context_budget_exceeded"
    assert provider.requests == []


def test_non_text_context_source_is_rejected_by_the_harness_reader() -> None:
    """An Adapter contract violation stops before any model request is sent."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ContextSourceBinding, build_harness

    client, provider = _client()
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    context_sources=("operation",),
                ),
            ),
            models=(_model(),),
            client=client,
            context_sources=(
                ContextSourceBinding(
                    name="operation",
                    read=lambda: None,  # type: ignore[arg-type, return-value]
                ),
            ),
        )
    ).start_main_agent("main")

    with pytest.raises(TypeError, match="Context Source must return text: operation"):
        agent.run(AgentTask(objective="Inspect."))

    assert provider.requests == []


def test_stable_prefix_keeps_all_names_and_omits_later_descriptions_in_order() -> None:
    """The 24k prefix spends description space in declared Profile order."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.skills import SkillDefinition, SkillManifest

    skills = tuple(
        SkillDefinition(
            manifest=SkillManifest(
                name=f"skill-{index:02d}",
                description=f"DESCRIPTION-{index:02d}-" + "D" * 1_970,
            ),
            instructions=f"Instructions {index}",
        )
        for index in range(15)
    )
    client, provider = _client(_completion())
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    skill_names=tuple(skill.name for skill in skills),
                ),
            ),
            models=(_model(),),
            client=client,
            skills=skills,
        )
    ).start_main_agent("main")

    result = agent.run(AgentTask(objective="Inspect metadata."))

    system = provider.requests[0].messages[0].content
    assert result.status == "completed"
    assert len(system) <= 24_000
    assert all(skill.name in system for skill in skills)
    assert "DESCRIPTION-00" in system
    assert "DESCRIPTION-14" not in system
    assert "[DESCRIPTION OMITTED: stable prefix budget]" in system


def test_stable_prefix_rejects_essential_names_before_model_use() -> None:
    """Complete Skill names are never clipped to force an impossible request."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.skills import SkillDefinition, SkillManifest

    skills = tuple(
        SkillDefinition(
            manifest=SkillManifest(
                name=f"skill-{index:03d}-" + "n" * 100,
                description="Bounded description.",
            ),
            instructions="Bounded instructions.",
        )
        for index in range(220)
    )
    client, provider = _client()
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    skill_names=tuple(skill.name for skill in skills),
                ),
            ),
            models=(_model(),),
            client=client,
            skills=skills,
        )
    ).start_main_agent("main")

    result = agent.run(AgentTask(objective="Do not clip authorization names."))

    assert result.status == "context_budget_exceeded"
    assert provider.requests == []


def test_compaction_reanchors_context_but_not_loaded_skill_instructions() -> None:
    """Current source state returns after compaction; Skill bodies remain reloadable."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import (
        AgentRuntimeDefinition,
        ContextSourceBinding,
        ToolBindingFactory,
        build_harness,
    )
    from restscope.llm import LLMResponse, ToolCall
    from restscope.skills import SkillDefinition, SkillManifest
    from restscope.tools import ToolBinding

    skill_body = "SKILL BODY MUST BE LOADED AGAIN AFTER COMPACTION"
    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(id="read-skill", name="skill.read", arguments={"name": "inspect"})
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="large-tool",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            content="The Skill guided an inspection; current Context remains authoritative.",
        ),
        _completion(),
    )
    agent = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                    skill_names=("inspect",),
                    context_sources=("operation",),
                ),
            ),
            models=(_model(context_window_tokens=2_400, max_tokens=200),),
            client=client,
            skills=(
                SkillDefinition(
                    manifest=SkillManifest(
                        name="inspect",
                        description="Inspect one operation.",
                        required_tools=("openapi.list_inputs",),
                        required_context_sources=("operation",),
                    ),
                    instructions=skill_body,
                ),
            ),
            context_sources=(
                ContextSourceBinding(
                    name="operation",
                    read=lambda: "## Current operation\nGET /pets",
                ),
            ),
            tool_binding_factories=(
                ToolBindingFactory(
                    name="openapi.list_inputs",
                    create=lambda: ToolBinding(
                        name="openapi.list_inputs",
                        execute=lambda operation_key: {
                            "content": "X" * 7_000,
                            "structured": {
                                "operation_key": operation_key,
                                "inputs": [],
                                "total": 0,
                                "offset": 0,
                            },
                        },
                    ),
                ),
            ),
        )
    ).start_main_agent("main")

    result = agent.run(AgentTask(objective="Inspect."))

    assert result.status == "completed"
    assert provider.requests[2].metadata["purpose"] == "context_compaction"
    final_messages = provider.requests[3].messages
    assert any("COMPACTED AGENT HISTORY" in item.content for item in final_messages)
    assert any("## Current operation\nGET /pets" in item.content for item in final_messages)
    assert all(skill_body not in item.content for item in final_messages)


def test_private_prompt_session_is_not_exported_from_agent_facade() -> None:
    """Callers receive Agent behavior, not a public Prompt assembly platform."""
    import restscope.agent as agent

    assert not hasattr(agent, "AgentPromptSession")
