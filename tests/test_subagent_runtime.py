"""Protect Profile-authorized asynchronous Subagent lifecycle behavior."""

from __future__ import annotations

from threading import Event

import pytest

from tests.agent_helpers import start_test_agent


class _ChildProvider:
    """Finish child Profiles locally, optionally waiting for test release."""

    name = "scripted"

    def __init__(self, release: Event | None = None) -> None:
        """Keep a cooperative gate and observable requests."""
        self.release = release
        self.requests = []

    def invoke(self, request):
        """Return one valid completion after the optional gate opens."""
        from restscope.llm import LLMResponse

        self.requests.append(request)
        if self.release is not None:
            self.release.wait(timeout=5)
        return LLMResponse(
            provider="scripted",
            model=request.model,
            parsed_json={
                "summary": f"{request.metadata['role']} finished.",
                "findings": [],
            },
            prompt_tokens=100,
            completion_tokens=20,
        )


def _runtime(*, release: Event | None = None, max_open_agents: int = 4):
    """Build one Main-to-child authorization graph without a business Profile."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    provider = _ChildProvider(release)
    providers = LLMProviderRegistry()
    providers.register(provider)
    client = LLMClient(providers)
    model = LLMModelConfig(
        name="fast",
        provider="scripted",
        model="fast-model",
        max_tokens=512,
        context_window_tokens=8_192,
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="main",
                    model_config_name="fast",
                    reasoning_effort="none",
                    tool_names=(
                        "subagent.start",
                        "subagent.wait",
                        "subagent.cancel",
                    ),
                    subagent_profile_names=("child",),
                ),
                AgentProfile(
                    name="child",
                    description="Inspect one bounded fact for the parent.",
                    model_config_name="fast",
                    reasoning_effort="none",
                ),
            ),
            models=(model,),
            client=client,
            max_open_agents=max_open_agents,
        )
    )
    return runtime, provider


def test_subagent_tools_are_global_closed_and_deep_contracts() -> None:
    """The global Catalog exposes fixed async protocols rather than open JSON."""
    from restscope.tools import builtin_tool_catalog

    definitions = builtin_tool_catalog().definitions(subject="subagent")

    assert [definition.name for definition in definitions] == [
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
    ]
    for definition in definitions:
        assert definition.spec.input_schema["additionalProperties"] is False
        assert definition.spec.output_schema["additionalProperties"] is False
    assert (
        definitions[0].spec.input_schema["properties"]["objective"]["maxLength"]
        == 12_000
    )
    assert (
        definitions[1].spec.input_schema["properties"]["subagent_ids"]["maxItems"] == 3
    )
    wait_schema = definitions[1].spec.output_schema
    snapshot_name = wait_schema["properties"]["agents"]["items"]["$ref"].split("/")[-1]
    assert wait_schema["$defs"][snapshot_name]["properties"]["completion"]


def test_profile_child_grants_require_all_subagent_tools_and_a_bounded_dag() -> None:
    """Invalid child authorization fails while Harness construction is atomic."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    providers = LLMProviderRegistry()
    providers.register(_ChildProvider())
    definition = {
        "models": (
            LLMModelConfig(
                name="fast",
                provider="scripted",
                model="fast-model",
                max_tokens=128,
                context_window_tokens=2_048,
            ),
        ),
        "client": LLMClient(providers),
    }

    with pytest.raises(ValueError, match="all three Subagent Tools"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(
                profiles=(
                    AgentProfile(
                        name="main",
                        model_config_name="fast",
                        reasoning_effort="none",
                        tool_names=("subagent.start",),
                        subagent_profile_names=("child",),
                    ),
                    AgentProfile(
                        name="child",
                        description="Inspect one bounded fact for the parent.",
                        model_config_name="fast",
                        reasoning_effort="none",
                    ),
                ),
                **definition,
            )
        )

    profiles = tuple(
        AgentProfile(
            name=f"level-{index}",
            description=f"Run bounded level {index} work.",
            model_config_name="fast",
            reasoning_effort="none",
            tool_names=(
                "subagent.start",
                "subagent.wait",
                "subagent.cancel",
            )
            if index < 4
            else (),
            subagent_profile_names=(f"level-{index + 1}",) if index < 4 else (),
        )
        for index in range(5)
    )
    with pytest.raises(ValueError, match="maximum Subagent depth"):
        build_harness(
            agent_runtime=AgentRuntimeDefinition(profiles=profiles, **definition)
        )


def test_start_wait_collects_direct_child_and_releases_open_slot() -> None:
    """A completed child stays open until its parent first collects the result."""
    from restscope.llm import ToolCall

    runtime, _provider = _runtime(max_open_agents=2)
    main = start_test_agent(runtime)

    started = main.toolbox.execute(
        ToolCall(
            id="start-1",
            name="subagent.start",
            arguments={"profile_name": "child", "objective": "Inspect one fact."},
        )
    )
    child_id = started.structured["subagent_id"]
    blocked = main.toolbox.execute(
        ToolCall(
            id="start-2",
            name="subagent.start",
            arguments={"profile_name": "child", "objective": "Use the last slot."},
        )
    )
    collected = main.toolbox.execute(
        ToolCall(
            id="wait-1",
            name="subagent.wait",
            arguments={"subagent_ids": [child_id], "timeout_seconds": 5},
        )
    )
    if collected.structured["agents"][0]["status"] == "running":
        collected = main.toolbox.execute(
            ToolCall(
                id="wait-2",
                name="subagent.wait",
                arguments={"subagent_ids": [child_id], "timeout_seconds": 5},
            )
        )
    restarted = main.toolbox.execute(
        ToolCall(
            id="start-3",
            name="subagent.start",
            arguments={"profile_name": "child", "objective": "Slot is reusable."},
        )
    )

    assert started.status == "succeeded"
    assert blocked.error["code"] == "subagent_capacity_exceeded"
    assert collected.structured["agents"][0]["status"] == "completed"
    assert (
        collected.structured["agents"][0]["completion"]["summary"] == "child finished."
    )
    assert restarted.status == "succeeded"


def test_wait_timeout_cancel_and_non_child_access_are_safe() -> None:
    """Wait never cancels, cancellation is cooperative, and ownership is strict."""
    from restscope.llm import ToolCall

    release = Event()
    runtime, _provider = _runtime(release=release)
    main = start_test_agent(runtime)
    started = main.toolbox.execute(
        ToolCall(
            id="start",
            name="subagent.start",
            arguments={"profile_name": "child", "objective": "Wait for release."},
        )
    )
    child_id = started.structured["subagent_id"]

    timed_out = main.toolbox.execute(
        ToolCall(
            id="wait",
            name="subagent.wait",
            arguments={"subagent_ids": [child_id], "timeout_seconds": 1},
        )
    )
    denied = main.toolbox.execute(
        ToolCall(
            id="foreign",
            name="subagent.wait",
            arguments={"subagent_ids": ["agent_not_a_child"], "timeout_seconds": 1},
        )
    )
    cancelled = main.toolbox.execute(
        ToolCall(
            id="cancel",
            name="subagent.cancel",
            arguments={"subagent_id": child_id, "reason": "No longer needed."},
        )
    )
    release.set()

    assert timed_out.status == "succeeded"
    assert timed_out.structured["timed_out"] is True
    assert timed_out.structured["agents"][0]["status"] in {"queued", "running"}
    assert denied.error["code"] == "subagent_not_direct_child"
    assert cancelled.structured["status"] == "cancellation_requested"
