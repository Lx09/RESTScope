"""Protect Profile-authorized construction through the public Harness seam."""

from __future__ import annotations

import pytest


class _ScriptedProvider:
    """Return prebuilt provider-neutral responses without external I/O."""

    name = "scripted"

    def __init__(self, responses) -> None:
        """Retain ordered responses and observable requests."""
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next response while recording the complete request."""
        self.requests.append(request)
        return self.responses.pop(0)


def _client(*responses):
    """Build the real LLMClient around one local provider Adapter."""
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry

    provider = _ScriptedProvider(responses)
    registry = LLMProviderRegistry()
    registry.register(provider)
    return LLMClient(registry), provider


def _model(name: str = "thinking"):
    """Return one enabled model configuration keyed by Profile name."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role=name,
        provider="scripted",
        model=f"{name}-model",
        max_tokens=512,
        context_window_tokens=8_192,
    )


def test_harness_starts_one_reusable_main_agent_from_an_authoritative_profile() -> None:
    """A Profile resolves before launch and determines the provider request."""
    from restscope.agent import AgentCompletion, AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMResponse

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "First task complete.", "findings": []},
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Second task complete.", "findings": []},
            prompt_tokens=140,
            completion_tokens=20,
            total_tokens=160,
        ),
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(name="main", model_config_name="thinking"),
            ),
            models=(_model(),),
            client=client,
        )
    )

    agent = runtime.start_main_agent("main")
    first = agent.run(AgentTask(objective="Inspect the first bounded task."))
    second = agent.run(AgentTask(objective="Continue with the second task."))

    assert first.status == "completed"
    assert first.completion == AgentCompletion(
        summary="First task complete.",
        findings=[],
    )
    assert second.status == "completed"
    assert [request.model for request in provider.requests] == [
        "thinking-model",
        "thinking-model",
    ]
    assert "Inspect the first bounded task." in provider.requests[1].messages[1].content
    assert any(
        "Continue with the second task." in message.content
        for message in provider.requests[1].messages
    )

    with pytest.raises(ValueError, match="Main Agent is already started"):
        runtime.start_main_agent("main")

    agent.close()
    replacement = runtime.start_main_agent("main")
    assert replacement is not agent


def test_main_agent_start_blocks_until_completion_without_an_agent_task() -> None:
    """The App-lifetime Main loop starts from Profile guidance, not a task DTO."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMResponse

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Main loop complete.", "findings": []},
        )
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    instructions="Own the App-lifetime API testing loop.",
                    model_config_name="thinking",
                ),
            ),
            models=(_model(),),
            client=client,
        )
    )
    agent = runtime.start_main_agent("main")

    assert agent.start() is None
    assert len(provider.requests) == 1
    assert any(
        "MAIN AGENT LOOP START" in message.content
        for message in provider.requests[0].messages
    )
    assert all(
        "AGENT TASK" not in message.content
        for message in provider.requests[0].messages
    )

    with pytest.raises(RuntimeError, match="already started"):
        agent.start()


def test_subagent_cannot_use_the_taskless_main_start_protocol() -> None:
    """Children always need the complete bounded objective supplied by a parent."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness

    client, provider = _client()
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=(
                        "subagent.start",
                        "subagent.wait",
                        "subagent.cancel",
                    ),
                    subagent_profile_names=("child",),
                ),
                AgentProfile(
                    name="child",
                    description="Handle one bounded delegated task.",
                    model_config_name="thinking",
                ),
            ),
            models=(_model(),),
            client=client,
        )
    )
    main = runtime.start_main_agent("main")
    child = main.tree_control._build_child(
        "child",
        main.tree_control,
        1,
        main.session_id,
        "agent_child",
        main.cancel_event,
    )

    with pytest.raises(RuntimeError, match="Main Agent"):
        child.start()

    assert provider.requests == []


def test_blocking_main_start_raises_safe_terminal_runtime_failures() -> None:
    """A void App entry cannot silently treat cancellation as completion."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness

    client, provider = _client()
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(AgentProfile(name="main", model_config_name="thinking"),),
            models=(_model(),),
            client=client,
        )
    )
    agent = runtime.start_main_agent("main")
    agent.cancel_event.set()

    with pytest.raises(RuntimeError, match="agent_cancelled"):
        agent.start()

    assert provider.requests == []


def test_unconfigured_harness_returns_a_stable_startup_error() -> None:
    """Default Operation Smoke composition remains valid but has no Main Profile."""
    from restscope.harness import AgentRuntimeNotConfiguredError, build_harness

    with pytest.raises(AgentRuntimeNotConfiguredError) as caught:
        build_harness().start_main_agent("missing")

    assert caught.value.code == "agent_runtime_not_configured"


def test_profile_registry_rejects_unknown_access_and_child_cycles_before_launch() -> None:
    """Invalid authorization graphs never create a Main Agent or model call."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness

    client, provider = _client()
    with pytest.raises(ValueError, match="Unknown model configuration"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(name="main", model_config_name="missing"),
                ),
                models=(_model(),),
                client=client,
            )
        )

    with pytest.raises(ValueError, match="cycle"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        description="Cycle participant main.",
                        model_config_name="thinking",
                        tool_names=(
                            "subagent.start",
                            "subagent.wait",
                            "subagent.cancel",
                        ),
                        subagent_profile_names=("child",),
                    ),
                    AgentProfile(
                        name="child",
                        description="Cycle participant child.",
                        model_config_name="thinking",
                        tool_names=(
                            "subagent.start",
                            "subagent.wait",
                            "subagent.cancel",
                        ),
                        subagent_profile_names=("main",),
                    ),
                ),
                models=(_model(),),
                client=client,
            )
        )

    assert provider.requests == []


def test_runtime_rejects_catalog_collisions_and_unowned_binding_factories() -> None:
    """All Catalog and Binding names are checked even before Profile selection."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory
    from restscope.harness.agent_runtime import AgentRuntimeResolver
    from restscope.llm import ToolSpec
    from restscope.tools import ToolBinding, ToolCatalog, ToolDefinition

    client, _provider = _client()
    duplicate = ToolDefinition(
        subject="external",
        spec=ToolSpec(
            name="openapi.list_inputs",
            description="External collision.",
            kind="mcp_tool",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    )
    base = AgentRuntimeDefinition(
        profiles=(AgentProfile(name="main", model_config_name="thinking"),),
        models=(_model(),),
        client=client,
    )
    with pytest.raises(ValueError, match="built-in and external"):
        AgentRuntimeResolver(base, external_catalog=ToolCatalog((duplicate,)))

    with pytest.raises(ValueError, match="Unknown Tool Binding factory"):
        AgentRuntimeResolver(
            AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(name="main", model_config_name="thinking"),
                ),
                models=(_model(),),
                client=client,
                tool_binding_factories=(
                    ToolBindingFactory(
                        name="not.in.catalog",
                        create=lambda: ToolBinding(
                            name="not.in.catalog",
                            execute=lambda: {},
                        ),
                    ),
                ),
            )
        )


def test_runtime_rejects_disabled_provider_and_missing_profile_dependencies() -> None:
    """Every model, Skill, Context Source, Tool Binding, and child resolves early."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.skills import SkillDefinition, SkillManifest

    client, _provider = _client()
    with pytest.raises(ValueError, match="disabled"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(AgentProfile(name="main", model_config_name="thinking"),),
                models=(_model().model_copy(update={"enabled": False}),),
                client=client,
            )
        )

    with pytest.raises(ValueError, match="Unknown model provider"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(AgentProfile(name="main", model_config_name="thinking"),),
                models=(_model(),),
                client=LLMClient(LLMProviderRegistry()),
            )
        )

    skill = SkillDefinition(
        manifest=SkillManifest(
            name="inspect",
            description="Inspect with explicit dependencies.",
            required_tools=("openapi.list_inputs",),
            required_context_sources=("operation",),
        ),
        instructions="Inspect only the granted operation.",
    )
    with pytest.raises(ValueError, match="requires Tool"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="thinking",
                        skill_names=("inspect",),
                        context_sources=("operation",),
                    ),
                ),
                models=(_model(),),
                client=client,
                skills=(skill,),
            )
        )

    with pytest.raises(ValueError, match="Unknown context source"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="thinking",
                        context_sources=("missing",),
                    ),
                ),
                models=(_model(),),
                client=client,
            )
        )

    with pytest.raises(ValueError, match="Missing Tool Binding"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="thinking",
                        tool_names=("openapi.list_inputs",),
                    ),
                ),
                models=(_model(),),
                client=client,
            )
        )


def test_agent_profile_uses_model_configuration_and_explicit_child_names() -> None:
    """The public Profile contains authorization names, not runtime objects."""
    from pydantic import ValidationError

    from restscope.agent import AgentProfile

    profile = AgentProfile(
        name="main",
        model_config_name="thinking",
        subagent_profile_names=("research",),
    )

    assert profile.model_config_name == "thinking"
    assert profile.subagent_profile_names == ("research",)
    assert "model_role" not in type(profile).model_fields
    with pytest.raises(ValidationError):
        AgentProfile(
            name="main",
            model_config_name="thinking",
            model_role="legacy",
        )


def test_child_profile_requires_description_before_any_agent_starts() -> None:
    """A parent cannot advertise an unexplained child Profile to the model."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness

    client, provider = _client()
    with pytest.raises(ValueError, match="child Profile.*description"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="thinking",
                        tool_names=(
                            "subagent.start",
                            "subagent.wait",
                            "subagent.cancel",
                        ),
                        subagent_profile_names=("child",),
                    ),
                    AgentProfile(name="child", model_config_name="thinking"),
                ),
                models=(_model(),),
                client=client,
            )
        )

    assert provider.requests == []


def test_generic_agent_rejects_construction_outside_the_harness() -> None:
    """Resolved runtime dependencies cannot be assembled through public code."""
    from restscope.agent import Agent, AgentProfile
    from restscope.tools import AgentToolbox

    client, _provider = _client()
    with pytest.raises(RuntimeError, match="constructed by HarnessRuntime"):
        Agent(
            profile=AgentProfile(name="main", model_config_name="thinking"),
            client=client,
            toolbox=AgentToolbox(),
        )


def test_profile_resolves_exact_skill_context_and_tool_binding_into_agent() -> None:
    """Only grants named by the Profile reach its system text and Tool payload."""
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

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Inspected the allowed operation.", "findings": []},
        ),
    )
    calls: list[str] = []

    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                    skill_names=("inspect_openapi",),
                    context_sources=("current_operation",),
                ),
            ),
            models=(_model(),),
            client=client,
            skills=(
                SkillDefinition(
                    manifest=SkillManifest(
                        name="inspect_openapi",
                        description="Inspect one operation.",
                        required_tools=("openapi.list_inputs",),
                        required_context_sources=("current_operation",),
                    ),
                    instructions="Use schema handles before drawing conclusions.",
                ),
            ),
            context_sources=(
                ContextSourceBinding(
                    name="current_operation",
                    read=lambda: "GET /pets is authorized context.",
                ),
            ),
            tool_binding_factories=(
                ToolBindingFactory(
                    name="openapi.list_inputs",
                    create=lambda: ToolBinding(
                        name="openapi.list_inputs",
                        execute=lambda operation_key: (
                            calls.append(operation_key)
                            or {
                                "structured": {
                                    "operation_key": operation_key,
                                    "inputs": [],
                                    "total": 0,
                                    "offset": 0,
                                }
                            }
                        ),
                    ),
                ),
            ),
        )
    )

    result = runtime.start_main_agent("main").run(
        AgentTask(objective="Inspect the current operation.")
    )

    assert result.status == "completed"
    assert calls == ["GET /pets"]
    assert [tool.name for tool in provider.requests[0].tools] == [
        "openapi.list_inputs",
        "skill.read",
    ]
    assert "Inspect one operation." in provider.requests[0].messages[0].content
    assert "Use schema handles" not in provider.requests[0].messages[0].content
    assert "GET /pets is authorized context." in provider.requests[0].messages[1].content
    assert provider.requests[1].messages[-1].role == "tool"


def test_agent_corrects_mixed_and_invalid_tool_outputs_without_executing_them() -> None:
    """Malformed model turns receive feedback and cannot change Tool state."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.tools import ToolBinding

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "must not be accepted", "findings": []},
            tool_calls=[
                ToolCall(
                    id="mixed",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="bad-args",
                    name="openapi.list_inputs",
                    arguments={},
                )
            ],
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Recovered safely.", "findings": []},
        ),
    )
    calls: list[str] = []
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                ),
            ),
            models=(_model(),),
            client=client,
            tool_binding_factories=(
                ToolBindingFactory(
                    name="openapi.list_inputs",
                    create=lambda: ToolBinding(
                        name="openapi.list_inputs",
                        execute=lambda operation_key: calls.append(operation_key),
                    ),
                ),
            ),
        )
    )

    result = runtime.start_main_agent("main").run(AgentTask(objective="Recover."))

    assert result.status == "completed"
    assert calls == []
    assert len(provider.requests) == 3
    assert any(
        "one Tool Call or one final result" in message.content
        for message in provider.requests[1].messages
    )
    assert provider.requests[2].messages[-1].role == "tool"


def test_shared_rollout_budget_uses_cached_input_and_blocks_overage_action() -> None:
    """The over-budget response is charged but its Tool Call never executes."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.tools import ToolBinding

    client, _provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="over-budget",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
            prompt_tokens=100,
            cached_input_tokens=40,
            completion_tokens=10,
        )
    )
    calls: list[str] = []
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                ),
            ),
            models=(_model(),),
            client=client,
            rollout_budget_weighted_tokens=15,
            tool_binding_factories=(
                ToolBindingFactory(
                    name="openapi.list_inputs",
                    create=lambda: ToolBinding(
                        name="openapi.list_inputs",
                        execute=lambda operation_key: calls.append(operation_key),
                    ),
                ),
            ),
        )
    )

    result = runtime.start_main_agent("main").run(AgentTask(objective="Stay safe."))

    assert result.status == "rollout_budget_exceeded"
    assert result.usage.weighted_tokens == 16
    assert result.usage.cached_input_tokens == 40
    assert calls == []


def test_rollout_budget_reminder_is_injected_once_after_crossing() -> None:
    """Crossing a waterline adds one bounded reminder to the next model turn."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.tools import ToolBinding

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="call",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
            prompt_tokens=10,
            completion_tokens=50_001,
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Finished after reminder.", "findings": []},
            prompt_tokens=10,
            completion_tokens=1,
        ),
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                ),
            ),
            models=(_model(),),
            client=client,
            rollout_budget_weighted_tokens=100_000,
            tool_binding_factories=(
                ToolBindingFactory(
                    name="openapi.list_inputs",
                    create=lambda: ToolBinding(
                        name="openapi.list_inputs",
                        execute=lambda operation_key: {
                            "structured": {
                                "operation_key": operation_key,
                                "inputs": [],
                                "total": 0,
                                "offset": 0,
                            }
                        },
                    ),
                ),
            ),
        )
    )

    result = runtime.start_main_agent("main").run(AgentTask(objective="Finish."))

    reminders = [
        message.content
        for message in provider.requests[1].messages
        if "SHARED ROLLOUT BUDGET" in message.content
    ]
    assert result.status == "completed"
    assert len(reminders) == 1
    assert "50% of its weighted-token budget" in reminders[0]


def test_agent_compacts_at_eighty_percent_with_same_model_and_no_tools() -> None:
    """Large history is summarized atomically before the next ordinary turn."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.tools import ToolBinding

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="large-result",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
            prompt_tokens=100,
            completion_tokens=10,
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            content="The operation has no declared inputs; continue with that fact.",
            prompt_tokens=200,
            cached_input_tokens=50,
            completion_tokens=20,
        ),
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            parsed_json={"summary": "Completed after compaction.", "findings": []},
            prompt_tokens=80,
            completion_tokens=10,
        ),
    )
    small_model = _model().model_copy(
        update={"context_window_tokens": 2_000, "max_tokens": 200}
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                ),
            ),
            models=(small_model,),
            client=client,
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
    )

    result = runtime.start_main_agent("main").run(AgentTask(objective="Inspect."))

    compact_request = provider.requests[1]
    final_request = provider.requests[2]
    assert result.status == "completed"
    assert result.usage.model_outputs == 3
    assert result.usage.cached_input_tokens == 50
    assert compact_request.model == "thinking-model"
    assert compact_request.tools == []
    assert compact_request.tool_choice == "none"
    assert compact_request.response_format == "text"
    assert any(
        "COMPACTED AGENT HISTORY" in message.content
        for message in final_request.messages
    )


def test_agent_returns_stable_failure_after_two_invalid_compactions() -> None:
    """Two blank summaries preserve history and end without a normal model turn."""
    from restscope.agent import AgentProfile, AgentTask
    from restscope.harness import AgentRuntimeDefinition, ToolBindingFactory, build_harness
    from restscope.llm import LLMResponse, ToolCall
    from restscope.tools import ToolBinding

    client, provider = _client(
        LLMResponse(
            provider="scripted",
            model="thinking-model",
            tool_calls=[
                ToolCall(
                    id="large-result",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "GET /pets"},
                )
            ],
        ),
        LLMResponse(provider="scripted", model="thinking-model", content=""),
        LLMResponse(provider="scripted", model="thinking-model", content="   "),
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="thinking",
                    tool_names=("openapi.list_inputs",),
                ),
            ),
            models=(
                _model().model_copy(
                    update={"context_window_tokens": 2_000, "max_tokens": 200}
                ),
            ),
            client=client,
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
    )

    result = runtime.start_main_agent("main").run(AgentTask(objective="Inspect."))

    assert result.status == "context_compaction_failed"
    assert result.error.code == "context_compaction_failed"
    assert len(provider.requests) == 3
    assert all(request.tools == [] for request in provider.requests[1:])
