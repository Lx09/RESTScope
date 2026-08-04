"""Regression scenarios for llm deepseek. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class RecordingCompletions:
    def __init__(self, response: Any | None = None) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.response = response or deepseek_response(content='{"ok": true}')

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return self.response


class RecordingClient:
    def __init__(self, response: Any | None = None) -> None:
        self.chat = SimpleNamespace(completions=RecordingCompletions(response))


class SequencedCompletions:
    """Return a different provider response for each retry attempt."""

    def __init__(self, responses: list[Any]) -> None:
        """Keep the finite response script and every submitted request."""
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        """Return the next scripted response or expose an unexpected retry."""
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("DeepSeek provider made an unexpected retry")
        return self.responses.pop(0)


class SequencedClient:
    """Expose sequenced completions through the OpenAI-compatible shape."""

    def __init__(self, responses: list[Any]) -> None:
        """Create one recording completions endpoint for the provider."""
        self.chat = SimpleNamespace(completions=SequencedCompletions(responses))


class FailingCompletions:
    """Raise one SDK-like exception and retain the attempted request."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        raise self.exc


class FailingClient:
    """Expose a failing completions endpoint through the SDK client shape."""

    def __init__(self, exc: Exception) -> None:
        self.chat = SimpleNamespace(completions=FailingCompletions(exc))


def deepseek_response(
    *,
    content: str | None,
    tool_calls: list[Any] | None = None,
    reasoning_content: str | None = None,
) -> Any:
    return SimpleNamespace(
        id="deepseek-response",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                    reasoning_content=reasoning_content,
                ),
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def test_deepseek_provider_uses_official_endpoint_by_default() -> None:
    """Scenario: verify that deepseek provider uses official endpoint by default."""
    from restscope.llm.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(api_key="test-key", client=RecordingClient())

    assert provider.name == "deepseek"
    assert provider.base_url == "https://api.deepseek.com"


def test_deepseek_standard_and_beta_clients_share_three_sdk_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both official endpoints apply the same bounded model-request policy."""
    import openai

    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    constructed: list[dict[str, Any]] = []

    class FakeSDKClient(RecordingClient):
        """Record SDK constructor options while serving one harmless response."""

        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            super().__init__()

    monkeypatch.setattr(openai, "OpenAI", FakeSDKClient)
    provider = DeepSeekProvider(api_key="test-key")
    provider.invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[LLMMessage(role="user", content="Submit")],
            tools=[_strict_submit_tool()],
            tool_choice="required",
            reasoning=LLMReasoningConfig(mode="disabled"),
        )
    )

    assert [options["max_retries"] for options in constructed] == [3, 3]
    assert [str(options["base_url"]) for options in constructed] == [
        "https://api.deepseek.com",
        "https://api.deepseek.com/beta",
    ]


def test_deepseek_provider_translates_reasoning_and_json_schema_request() -> None:
    """Scenario: verify that deepseek provider translates reasoning and json schema request."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    provider = DeepSeekProvider(api_key="test-key", client=client)
    response = provider.invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[
                LLMMessage(role="system", content="Investigate the document."),
                LLMMessage(role="user", content="Return the result."),
            ],
            temperature=0.4,
            response_format="json_schema",
            json_schema_name="Result",
            json_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high"
    assert "temperature" not in kwargs
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "JSON" in kwargs["messages"][0]["content"]
    assert '"ok"' in kwargs["messages"][0]["content"]
    assert response.parsed_json == {"ok": True}


def test_deepseek_json_mode_does_not_duplicate_an_existing_json_instruction() -> None:
    """Scenario: verify that deepseek json mode does not duplicate an existing json instruction."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    system = (
        'Task: choose one option. Return JSON like {"choice":"A1"}. '
        "Do not explain."
    )

    DeepSeekProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content="[A1] first option"),
            ],
            response_format="json",
            reasoning=LLMReasoningConfig(mode="disabled"),
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["messages"][0]["content"] == system


def test_deepseek_provider_applies_configured_reasoning_without_agent_changes() -> None:
    """Scenario: verify that deepseek provider applies configured reasoning without agent changes."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    DeepSeekProvider(
        api_key="test-key",
        client=client,
        default_reasoning=LLMReasoningConfig(mode="enabled", effort="max"),
    ).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[LLMMessage(role="user", content="Investigate")],
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "max"


def test_deepseek_non_thinking_omits_reasoning_effort() -> None:
    """Scenario: verify that deepseek non thinking omits reasoning effort."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    DeepSeekProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[LLMMessage(role="user", content="Answer quickly")],
            temperature=0.2,
            reasoning=LLMReasoningConfig(mode="disabled", effort="max"),
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert kwargs["temperature"] == 0.2
    assert "reasoning_effort" not in kwargs


def _search_tool():
    from restscope.llm import ToolSpec

    return ToolSpec(
        name="catalog.search",
        description="Search catalog entries.",
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def _strict_submit_tool():
    """Return one minimal schema satisfying DeepSeek strict object rules."""
    from restscope.llm import ToolSpec

    return ToolSpec(
        name="submit_decision",
        description="Submit one decision.",
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {"action": {"enum": ["accept"]}},
            "required": ["action"],
            "additionalProperties": False,
        },
        strict=True,
    )


def _strict_tool_call() -> Any:
    """Return one provider-shaped strict submission call."""
    return SimpleNamespace(
        id="call_submit",
        type="function",
        function=SimpleNamespace(
            name="submit_decision",
            arguments='{"action":"accept"}',
        ),
    )


def test_deepseek_strict_tools_use_only_the_beta_endpoint() -> None:
    """A strict call is serialized to Beta while normal client stays unused."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    standard_client = RecordingClient()
    beta_client = RecordingClient(
        deepseek_response(
            content="",
            tool_calls=[_strict_tool_call()],
        )
    )
    response = DeepSeekProvider(
        api_key="test-key",
        client=standard_client,
        beta_client=beta_client,
    ).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[LLMMessage(role="user", content="Submit acceptance")],
            response_format="text",
            tools=[_strict_submit_tool()],
            tool_choice="required",
            reasoning=LLMReasoningConfig(mode="disabled"),
        )
    )

    assert standard_client.chat.completions.kwargs is None
    kwargs = beta_client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["tools"][0]["function"]["strict"] is True
    assert kwargs["tool_choice"] == "required"
    assert response.tool_calls[0].arguments == {"action": "accept"}
    assert response.metadata["strict_tool_beta"] is True


@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503, 599])
def test_deepseek_strict_beta_capacity_failure_retries_standard_url_once(
    status_code: int,
) -> None:
    """A Beta capacity outage gets one pre-tool fallback on the standard URL."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    beta_error = RuntimeError("service too busy")
    beta_error.status_code = status_code  # type: ignore[attr-defined]
    standard_client = RecordingClient(
        deepseek_response(
            content="",
            tool_calls=[_strict_tool_call()],
        )
    )
    beta_client = FailingClient(beta_error)
    response = DeepSeekProvider(
        api_key="test-key",
        client=standard_client,
        beta_client=beta_client,
    ).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[LLMMessage(role="user", content="Submit acceptance")],
            response_format="text",
            tools=[_strict_submit_tool()],
            tool_choice="required",
            reasoning=LLMReasoningConfig(mode="disabled"),
        )
    )

    assert len(beta_client.chat.completions.requests) == 1
    assert standard_client.chat.completions.kwargs is not None
    assert response.tool_calls[0].arguments == {"action": "accept"}
    assert response.metadata["strict_tool_beta"] is False


def test_deepseek_rejects_mixed_strict_tools_before_network() -> None:
    """DeepSeek Beta requires every function in one request to be strict."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import (
        DeepSeekCompatibilityError,
        DeepSeekProvider,
    )

    standard_client = RecordingClient()
    beta_client = RecordingClient()
    with pytest.raises(DeepSeekCompatibilityError) as exc_info:
        DeepSeekProvider(
            api_key="test-key",
            client=standard_client,
            beta_client=beta_client,
        ).invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                messages=[LLMMessage(role="user", content="Submit")],
                tools=[_strict_submit_tool(), _search_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="disabled"),
            )
        )

    assert exc_info.value.code == "deepseek_mixed_strict_tools"
    assert standard_client.chat.completions.kwargs is None
    assert beta_client.chat.completions.kwargs is None


def test_deepseek_custom_gateway_reports_strict_endpoint_unavailable() -> None:
    """A custom standard gateway is never silently rewritten to a Beta URL."""
    from restscope.llm import (
        LLMMessage,
        LLMReasoningConfig,
        LLMRequest,
        StrictToolUnavailableError,
    )
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://gateway.example/v1",
        client=client,
    )
    with pytest.raises(StrictToolUnavailableError) as exc_info:
        provider.invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                messages=[LLMMessage(role="user", content="Submit")],
                tools=[_strict_submit_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="disabled"),
            )
        )

    assert exc_info.value.code == "deepseek_strict_endpoint_unavailable"
    assert client.chat.completions.kwargs is None


@pytest.mark.parametrize("status_code", [400, 404, 405, 422])
def test_deepseek_beta_compatibility_failures_are_fallback_eligible(
    status_code: int,
) -> None:
    """Schema and route failures expose the narrow compatibility error."""
    from restscope.llm import (
        LLMMessage,
        LLMReasoningConfig,
        LLMRequest,
        StrictToolUnavailableError,
    )
    from restscope.llm.providers.deepseek import DeepSeekProvider

    error = RuntimeError("beta rejected request")
    error.status_code = status_code  # type: ignore[attr-defined]
    provider = DeepSeekProvider(
        api_key="test-key",
        client=RecordingClient(),
        beta_client=FailingClient(error),
    )

    with pytest.raises(StrictToolUnavailableError):
        provider.invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                messages=[LLMMessage(role="user", content="Submit")],
                tools=[_strict_submit_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="disabled"),
            )
        )


@pytest.mark.parametrize("status_code", [401, 403])
def test_deepseek_beta_account_failures_do_not_enable_fallback(
    status_code: int,
) -> None:
    """Credentials, permission, and capacity failures remain provider errors."""
    from restscope.llm import (
        LLMMessage,
        LLMReasoningConfig,
        LLMRequest,
        ProviderInvokeError,
        StrictToolUnavailableError,
    )
    from restscope.llm.providers.deepseek import DeepSeekProvider

    error = RuntimeError("account request rejected")
    error.status_code = status_code  # type: ignore[attr-defined]
    provider = DeepSeekProvider(
        api_key="test-key",
        client=RecordingClient(),
        beta_client=FailingClient(error),
    )

    with pytest.raises(ProviderInvokeError) as exc_info:
        provider.invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                messages=[LLMMessage(role="user", content="Submit")],
                tools=[_strict_submit_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="disabled"),
            )
        )

    assert not isinstance(exc_info.value, StrictToolUnavailableError)


@pytest.mark.parametrize(
    "network_error",
    [ConnectionError("connection refused"), TimeoutError("request timed out")],
)
def test_deepseek_beta_network_failures_retry_standard_url_once(
    network_error: Exception,
) -> None:
    """Beta connection and timeout exhaustion use the standard endpoint once."""
    from restscope.llm import (
        LLMMessage,
        LLMReasoningConfig,
        LLMRequest,
    )
    from restscope.llm.providers.deepseek import DeepSeekProvider

    standard_client = RecordingClient()
    provider = DeepSeekProvider(
        api_key="test-key",
        client=standard_client,
        beta_client=FailingClient(network_error),
    )

    response = provider.invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-flash",
            messages=[LLMMessage(role="user", content="Submit")],
            tools=[_strict_submit_tool()],
            tool_choice="required",
            reasoning=LLMReasoningConfig(mode="disabled"),
        )
    )

    assert standard_client.chat.completions.kwargs is not None
    assert response.metadata["strict_tool_beta"] is False


def test_deepseek_strict_standard_fallback_capacity_failure_propagates() -> None:
    """A second endpoint outage ends the model call without a third endpoint try."""
    from restscope.llm import (
        LLMMessage,
        LLMReasoningConfig,
        LLMRequest,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.deepseek import DeepSeekProvider

    beta_error = RuntimeError("beta busy")
    beta_error.status_code = 503  # type: ignore[attr-defined]
    standard_error = RuntimeError("standard busy")
    standard_error.status_code = 503  # type: ignore[attr-defined]
    beta_client = FailingClient(beta_error)
    standard_client = FailingClient(standard_error)
    provider = DeepSeekProvider(
        api_key="test-key",
        client=standard_client,
        beta_client=beta_client,
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        provider.invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-flash",
                messages=[LLMMessage(role="user", content="Submit")],
                tools=[_strict_submit_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="disabled"),
            )
        )

    assert caught.value.__cause__ is standard_error
    assert len(beta_client.chat.completions.requests) == 1
    assert len(standard_client.chat.completions.requests) == 1


def test_deepseek_configured_beta_url_keeps_normal_calls_on_standard_url() -> None:
    """An explicit Beta URL is split into standard and strict endpoint roles."""
    from restscope.llm.providers.deepseek import DeepSeekProvider

    provider = DeepSeekProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com/beta/",
        client=RecordingClient(),
        beta_client=RecordingClient(),
    )

    assert provider.base_url == "https://api.deepseek.com"
    assert provider._strict_base_url == "https://api.deepseek.com/beta"


def test_deepseek_thinking_auto_keeps_tools_but_omits_tool_choice() -> None:
    """Scenario: verify that deepseek thinking auto keeps tools but omits tool choice."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    DeepSeekProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[LLMMessage(role="user", content="Investigate")],
            tools=[_search_tool()],
            tool_choice="auto",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["tools"]
    assert "tool_choice" not in kwargs


def test_deepseek_thinking_none_omits_tools() -> None:
    """Scenario: verify that deepseek thinking none omits tools."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = RecordingClient()
    DeepSeekProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[LLMMessage(role="user", content="Summarize")],
            tools=[_search_tool()],
            tool_choice="none",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    kwargs = client.chat.completions.kwargs
    assert kwargs is not None
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


def test_deepseek_thinking_rejects_forced_tool_choice_before_network() -> None:
    """Scenario: verify that deepseek thinking rejects forced tool choice before network."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekCompatibilityError, DeepSeekProvider

    client = RecordingClient()
    with pytest.raises(DeepSeekCompatibilityError) as exc_info:
        DeepSeekProvider(api_key="test-key", client=client).invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-pro",
                messages=[LLMMessage(role="user", content="Investigate")],
                tools=[_search_tool()],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
            )
        )

    assert exc_info.value.code == "deepseek_tool_choice_unsupported"
    assert client.chat.completions.kwargs is None


def test_deepseek_thinking_rejects_forced_tool_choice_without_tools() -> None:
    """Scenario: verify that deepseek thinking rejects forced tool choice without tools."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekCompatibilityError, DeepSeekProvider

    client = RecordingClient()
    with pytest.raises(DeepSeekCompatibilityError) as exc_info:
        DeepSeekProvider(api_key="test-key", client=client).invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-pro",
                messages=[LLMMessage(role="user", content="Investigate")],
                tool_choice="required",
                reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
            )
        )

    assert exc_info.value.code == "deepseek_tool_choice_unsupported"
    assert client.chat.completions.kwargs is None


def _provider_tool_call() -> Any:
    return SimpleNamespace(
        id="call_search",
        type="function",
        function=SimpleNamespace(
            name="openapi_search_symbols",
            arguments='{"query":"orderId"}',
        ),
    )


def test_deepseek_provider_carries_and_replays_tool_call_reasoning() -> None:
    """Scenario: verify that deepseek provider carries and replays tool call reasoning."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    first_client = RecordingClient(
        deepseek_response(
            content="",
            tool_calls=[_provider_tool_call()],
            reasoning_content="I should search for orderId producers.",
        )
    )
    provider = DeepSeekProvider(api_key="test-key", client=first_client)
    first_response = provider.invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[LLMMessage(role="user", content="Investigate")],
            tools=[_search_tool()],
            tool_choice="auto",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    assert first_response.tool_calls[0].provider_context == {
        "reasoning_content": "I should search for orderId producers."
    }

    second_client = RecordingClient()
    provider.client = second_client
    provider.invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[
                LLMMessage(role="user", content="Investigate"),
                LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=first_response.tool_calls,
                ),
                LLMMessage(
                    role="tool",
                    content='{"matches": []}',
                    tool_call_id="call_search",
                ),
            ],
            tools=[_search_tool()],
            tool_choice="auto",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    messages = second_client.chat.completions.kwargs["messages"]
    assistant_message = next(message for message in messages if message["role"] == "assistant")
    assert assistant_message["reasoning_content"] == "I should search for orderId producers."


def test_deepseek_provider_rejects_tool_response_without_reasoning() -> None:
    """Scenario: verify that deepseek provider rejects tool response without reasoning."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekCompatibilityError, DeepSeekProvider

    provider = DeepSeekProvider(
        api_key="test-key",
        client=RecordingClient(
            deepseek_response(
                content="",
                tool_calls=[_provider_tool_call()],
                reasoning_content=None,
            )
        ),
    )

    with pytest.raises(DeepSeekCompatibilityError) as exc_info:
        provider.invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-pro",
                messages=[LLMMessage(role="user", content="Investigate")],
                tools=[_search_tool()],
                tool_choice="auto",
                reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
            )
        )

    assert exc_info.value.code == "deepseek_reasoning_content_missing"


def test_deepseek_provider_retries_one_incomplete_tool_response() -> None:
    """A transient missing reasoning field is retried before any tool runs."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest
    from restscope.llm.providers.deepseek import DeepSeekProvider

    client = SequencedClient(
        [
            deepseek_response(
                content="",
                tool_calls=[_provider_tool_call()],
                reasoning_content=None,
            ),
            deepseek_response(
                content="",
                tool_calls=[_provider_tool_call()],
                reasoning_content="Search the catalog before deciding.",
            ),
        ]
    )

    response = DeepSeekProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="deepseek",
            model="deepseek-v4-pro",
            messages=[LLMMessage(role="user", content="Investigate")],
            tools=[_search_tool()],
            tool_choice="auto",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
        )
    )

    assert len(client.chat.completions.requests) == 2
    assert response.tool_calls[0].provider_context["reasoning_content"] == (
        "Search the catalog before deciding."
    )
    assert response.metadata["provider_retry_count"] == 1


def test_deepseek_provider_rejects_thinking_history_without_reasoning() -> None:
    """Scenario: verify that deepseek provider rejects thinking history without reasoning."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest, ToolCall
    from restscope.llm.providers.deepseek import DeepSeekCompatibilityError, DeepSeekProvider

    client = RecordingClient()
    with pytest.raises(DeepSeekCompatibilityError) as exc_info:
        DeepSeekProvider(api_key="test-key", client=client).invoke(
            LLMRequest(
                provider="deepseek",
                model="deepseek-v4-pro",
                messages=[
                    LLMMessage(role="user", content="Investigate"),
                    LLMMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_search",
                                name="catalog.search",
                                arguments={"query": "orderId"},
                                provider="deepseek",
                            )
                        ],
                    ),
                    LLMMessage(
                        role="tool",
                        content='{"matches": []}',
                        tool_call_id="call_search",
                    ),
                ],
                tools=[_search_tool()],
                tool_choice="auto",
                reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
            )
        )

    assert exc_info.value.code == "deepseek_reasoning_content_missing"
    assert client.chat.completions.kwargs is None
