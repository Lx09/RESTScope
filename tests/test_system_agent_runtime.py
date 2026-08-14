"""Protect repeatable Profile-authorized System Agent execution."""

from __future__ import annotations

from threading import Event, Thread

from pydantic import BaseModel, ConfigDict


class _Choice(BaseModel):
    """Represent the tiny result used to exercise Harness correction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    choice: str


class _ScriptedProvider:
    """Return ordered local responses and retain complete requests."""

    name = "scripted"

    def __init__(self, outputs: list[object]) -> None:
        """Store structured outputs without external model calls."""
        self.outputs = list(outputs)
        self.requests = []

    def invoke(self, request):
        """Return the next response and expose every correction prompt."""
        from restscope.llm import LLMResponse

        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, LLMResponse):
            return output
        return LLMResponse(
            provider=self.name,
            model=request.model,
            parsed_json=output,
            prompt_tokens=100,
            completion_tokens=10,
        )


def _runtime(
    *outputs: object,
    tool_names: tuple[str, ...] = (),
    provider_override: object | None = None,
    rollout_budget_weighted_tokens: float = 1_000_000,
):
    """Build one registered no-Tool System Profile through the public seam."""
    from restscope.agent import AgentProfile, SystemAgentTask
    from restscope.harness import (
        AgentRuntimeDefinition,
        SystemAgentDefinition,
        build_harness,
    )
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    provider = provider_override or _ScriptedProvider(list(outputs))
    registry = LLMProviderRegistry()
    registry.register(provider)
    definition = SystemAgentDefinition(
        profile_name="chooser",
        adapt_task=SystemAgentTask.model_validate,
        output_model=_Choice,
        output_schema_name="Choice",
        build_output_schema=lambda task: {
            "type": "object",
            "properties": {
                "choice": {
                    "type": "string",
                    "enum": list(task.allowed_result_aliases),
                }
            },
            "required": ["choice"],
            "additionalProperties": False,
        },
        validate_output=lambda output, task: (
            ()
            if _Choice.model_validate(output).choice in task.allowed_result_aliases
            else ("choice was not offered",)
        ),
    )
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(
                AgentProfile(
                    name="chooser",
                    instructions="Choose one offered alias.",
                    model_config_name="fast",
                    tool_names=tool_names,
                ),
            ),
            models=(
                LLMModelConfig(
                    name="fast",
                    provider="scripted",
                    model="fast-model",
                    max_tokens=512,
                    context_window_tokens=8_192,
                ),
            ),
            client=LLMClient(registry),
            system_agents=(definition,),
            rollout_budget_weighted_tokens=rollout_budget_weighted_tokens,
        )
    )
    return runtime, provider


def test_system_agent_retries_without_an_attempt_limit_until_output_is_valid() -> None:
    """Three invalid finals still receive Harness feedback before success."""
    from restscope.agent import SystemAgentTask

    runtime, provider = _runtime(
        {"choice": "I99"},
        {"choice": "I98"},
        {"choice": "I97"},
        {"choice": "I1"},
    )

    result = runtime.run_system_agent(
        "chooser",
        SystemAgentTask(
            objective="Choose one candidate.",
            allowed_result_aliases=("I1", "I2"),
        ),
    )

    assert result.status == "completed"
    assert result.output == {"choice": "I1"}
    assert result.usage.model_outputs == 4
    assert len(provider.requests) == 4
    assert provider.requests[0].json_schema["properties"]["choice"]["enum"] == [
        "I1",
        "I2",
    ]
    assert provider.requests[0].tools == []
    assert sum(
        "rejected by the Harness" in message.content
        for message in provider.requests[-1].messages
    ) == 3


def test_system_agent_invocations_use_unique_roots_and_can_repeat() -> None:
    """Synchronous calls never reuse hidden prompt or tree state."""
    from restscope.agent import SystemAgentTask

    runtime, provider = _runtime({"choice": "I1"}, {"choice": "I2"})
    task = SystemAgentTask(
        objective="Choose one candidate.",
        allowed_result_aliases=("I1", "I2"),
    )

    first = runtime.run_system_agent("chooser", task)
    second = runtime.run_system_agent(
        "chooser",
        {
            "objective": "Choose one candidate.",
            "allowed_result_aliases": ["I1", "I2"],
        },
    )

    assert first.session_id != second.session_id
    assert first.output == {"choice": "I1"}
    assert second.output == {"choice": "I2"}
    assert all(request.metadata["role"] == "chooser" for request in provider.requests)


def test_unregistered_profile_cannot_be_started_as_a_system_agent() -> None:
    """A normal Profile is not implicitly callable by deterministic code."""
    import pytest

    from restscope.agent import SystemAgentTask
    from restscope.harness import SystemAgentNotConfiguredError

    runtime, _provider = _runtime()

    with pytest.raises(SystemAgentNotConfiguredError, match="not registered"):
        runtime.run_system_agent(
            "missing",
            SystemAgentTask(objective="Choose.", allowed_result_aliases=("I1",)),
        )


def test_system_agent_receives_every_tool_granted_by_its_profile() -> None:
    """System lifecycle adds no Tool restriction beyond normal Profile rules."""
    from restscope.agent import SystemAgentTask
    from restscope.llm import LLMResponse, ToolCall

    runtime, provider = _runtime(
        LLMResponse(
            provider="scripted",
            model="fast-model",
            tool_calls=[
                ToolCall(id="plan-read", name="plan.read", arguments={})
            ],
        ),
        {"choice": "I1"},
        tool_names=("plan.read", "plan.update"),
    )

    result = runtime.run_system_agent(
        "chooser",
        SystemAgentTask(
            objective="Inspect the private Plan, then choose.",
            allowed_result_aliases=("I1",),
        ),
    )

    assert result.status == "completed"
    assert result.usage.tool_calls == 1
    assert [tool.name for tool in provider.requests[0].tools] == [
        "plan.read",
        "plan.update",
    ]


def test_system_agent_accounts_usage_without_enforcing_the_main_budget() -> None:
    """A System root completes even when its usage exceeds the normal limit."""
    from restscope.agent import SystemAgentTask

    runtime, _provider = _runtime(
        {"choice": "I1"},
        rollout_budget_weighted_tokens=1,
    )

    result = runtime.run_system_agent(
        "chooser",
        SystemAgentTask(
            objective="Choose one candidate.",
            allowed_result_aliases=("I1",),
        ),
    )

    assert result.status == "completed"
    assert result.usage.prompt_tokens == 100
    assert result.usage.output_tokens == 10


class _BlockingProvider(_ScriptedProvider):
    """Pause one model call so shutdown can cancel an active System root."""

    def __init__(self) -> None:
        """Create synchronization signals around one otherwise valid reply."""
        super().__init__([{"choice": "I1"}])
        self.entered = Event()
        self.release = Event()

    def invoke(self, request):
        """Wait until the test has asked the Harness to close."""
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super().invoke(request)


class _FailingProvider(_ScriptedProvider):
    """Raise a terminal Provider error before producing any model output."""

    def __init__(self) -> None:
        """Create an empty Provider whose only call fails."""
        super().__init__([])

    def invoke(self, request):
        """Simulate a non-recoverable Provider boundary failure."""
        self.requests.append(request)
        raise RuntimeError("provider stopped")


def test_provider_failure_stops_and_cleans_up_the_system_agent_tree() -> None:
    """A terminal Provider exception cannot leave a registered root behind."""
    import pytest

    provider = _FailingProvider()
    runtime, _provider = _runtime(provider_override=provider)

    with pytest.raises(RuntimeError, match="provider stopped"):
        runtime.run_system_agent(
            "chooser",
            {
                "objective": "Choose one candidate.",
                "allowed_result_aliases": ["I1"],
            },
        )

    assert runtime._system_agents == {}


def test_harness_close_cancels_an_active_system_agent_and_cleans_it_up() -> None:
    """App-style shutdown stops a root waiting inside its Provider call."""
    from restscope.agent import SystemAgentTask

    provider = _BlockingProvider()
    runtime, _provider = _runtime(provider_override=provider)
    results = []

    worker = Thread(
        target=lambda: results.append(
            runtime.run_system_agent(
                "chooser",
                SystemAgentTask(
                    objective="Choose one candidate.",
                    allowed_result_aliases=("I1",),
                ),
            )
        )
    )
    worker.start()
    assert provider.entered.wait(timeout=5)

    runtime.close_agents()
    provider.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert results[0].status == "cancelled"
    assert runtime._system_agents == {}
