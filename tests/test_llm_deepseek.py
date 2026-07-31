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
