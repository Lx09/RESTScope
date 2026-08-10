"""Regression scenarios for llm mvp. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel


class CampaignSpec(BaseModel):
    campaign_type: str
    target_operation_ids: list[str]
    hypothesis: str
    rationale: str


def test_llm_schema_serialization_and_import_smoke() -> None:
    """Scenario: verify that llm schema serialization and import smoke."""
    from restscope.llm import LLMClient, LLMMessage, LLMRequest, ToolSpec

    request = LLMRequest(
        provider="fake",
        model="fake-model",
        messages=[LLMMessage(role="user", content="hello")],
        tools=[
            ToolSpec(
                name="artifact.read_summary",
                description="Read summary",
                kind="local_function",
                input_schema={"type": "object"},
            )
        ],
    )

    assert LLMClient is not None
    assert request.model_dump(mode="json")["tools"][0]["name"] == "artifact.read_summary"


def test_model_config_is_named_without_a_semantic_role_selector() -> None:
    """Profiles choose exact config names instead of hidden role mappings."""
    from restscope.llm import LLMModelConfig

    thinking = LLMModelConfig(
        name="thinking",
        provider="stub",
        model="thinking-model",
    )
    fast = LLMModelConfig(name="fast", provider="stub", model="fast-model")

    assert thinking.name == "thinking"
    assert fast.name == "fast"


def test_llm_model_config_uses_large_context_defaults() -> None:
    """Direct role configs share the application model window defaults."""
    from restscope.llm import LLMModelConfig

    model = LLMModelConfig(
        name="thinking",
        provider="stub",
        model="think",
    )

    assert model.max_tokens == 8192
    assert model.context_window_tokens == 131072

    with pytest.raises(ValueError, match="smaller than context_window_tokens"):
        LLMModelConfig(
            name="invalid",
            provider="stub",
            model="think",
            max_tokens=4096,
            context_window_tokens=4096,
        )


def test_reasoning_config_and_tool_provider_context_round_trip() -> None:
    """Scenario: verify that reasoning config and tool provider context round trip."""
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest, ToolCall

    request = LLMRequest(
        provider="deepseek",
        model="deepseek-v4-pro",
        messages=[LLMMessage(role="user", content="investigate")],
        reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
    )
    tool_call = ToolCall(
        id="call_search",
        name="catalog.search",
        arguments={"query": "orderId"},
        provider="deepseek",
        provider_context={"reasoning_content": "Search for producer operations."},
    )

    restored_request = LLMRequest.model_validate(request.model_dump(mode="json"))
    restored_call = ToolCall.model_validate(tool_call.model_dump(mode="json"))

    assert restored_request.reasoning.mode == "enabled"
    assert restored_request.reasoning.effort == "high"
    assert restored_call.provider_context == {
        "reasoning_content": "Search for producer operations."
    }


@pytest.mark.parametrize("with_tool_call", [False, True])
def test_deepseek_preserves_raw_reasoning_on_every_response(
    with_tool_call: bool,
) -> None:
    """DeepSeek exposes raw reasoning for both Tool calls and final answers."""
    from restscope.llm import LLMMessage, LLMRequest, ToolSpec
    from restscope.llm.providers.deepseek import DeepSeekProvider

    tool_calls = (
        [
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(
                    name="catalog_lookup",
                    arguments='{"query":"projects"}',
                ),
            )
        ]
        if with_tool_call
        else []
    )
    message = SimpleNamespace(
        content=None if with_tool_call else '{"summary":"done"}',
        reasoning_content="Inspect the available evidence.",
        tool_calls=tool_calls,
    )
    response = SimpleNamespace(
        id="response-1",
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="tool_calls" if with_tool_call else "stop",
            )
        ],
        usage=None,
    )

    class Completions:
        def create(self, **kwargs: object) -> object:
            del kwargs
            return response

    provider = DeepSeekProvider(
        api_key="test-key",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        ),
    )
    request = LLMRequest(
        provider="deepseek",
        model="deepseek-reasoner",
        messages=[LLMMessage(role="user", content="Investigate")],
        tools=(
            [
                ToolSpec(
                    name="catalog.lookup",
                    description="Look up one catalog entry",
                    kind="local_function",
                    input_schema={"type": "object"},
                )
            ]
            if with_tool_call
            else []
        ),
        tool_choice="auto" if with_tool_call else "none",
    )

    normalized = provider.invoke(request)

    assert normalized.reasoning_content == "Inspect the available evidence."
    if with_tool_call:
        assert normalized.tool_calls[0].provider_context == {
            "reasoning_content": "Inspect the available evidence."
        }


def test_provider_without_reasoning_returns_no_reasoning_item_value() -> None:
    """Providers that do not support raw reasoning retain the null default."""
    from restscope.llm import LLMResponse

    response = LLMResponse(provider="stub", model="plain", content="done")

    assert response.reasoning_content is None


def test_llm_client_invokes_provider_once_and_propagates_failure() -> None:
    """Scenario: verify that llm client invokes provider once and propagates failure."""
    from restscope.llm import LLMClient, LLMMessage, LLMRequest, ProviderInvokeError
    from restscope.llm.providers.base import BaseLLMProvider
    from restscope.llm.registry import LLMProviderRegistry

    class FailingProvider(BaseLLMProvider):
        name = "failing"

        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, request):
            del request
            self.calls += 1
            raise ProviderInvokeError("failed")

    provider = FailingProvider()
    registry = LLMProviderRegistry()
    registry.register(provider)

    with pytest.raises(ProviderInvokeError):
        LLMClient(registry).invoke(
            LLMRequest(
                provider="failing",
                model="test",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )

    assert provider.calls == 1


def test_llm_public_contract_excludes_removed_legacy_surface() -> None:
    """Scenario: verify that llm public contract excludes removed legacy surface."""
    import inspect

    import restscope.llm as llm
    import restscope.llm.providers as providers
    from restscope.llm.output_validator import OutputValidator
    from restscope.llm.providers.base import BaseLLMProvider

    removed_llm_exports = {
        "LLMRequestFactory",
        "OutputValidationError",
        "ProviderRateLimitError",
        "ProviderTimeoutError",
    }

    assert removed_llm_exports.isdisjoint(llm.__all__)
    assert all(not hasattr(llm, name) for name in removed_llm_exports)
    assert "FakeProvider" not in providers.__all__
    assert not hasattr(providers, "FakeProvider")
    assert "context_package" not in inspect.signature(OutputValidator.validate).parameters
    assert not hasattr(BaseLLMProvider, "ainvoke")


def test_openai_compatible_provider_configures_three_sdk_retries() -> None:
    """The standard client gives one model request three SDK-managed retries."""
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(api_key="test-key")

    assert provider.client.max_retries == 3


def test_tool_call_does_not_retain_raw_provider_payload() -> None:
    """Scenario: verify that tool call does not retain raw provider payload."""
    from restscope.llm import ToolCall

    tool_call = ToolCall(id="call_1", name="catalog.inspect")

    assert "raw" not in type(tool_call).model_fields


class _FakeOpenAIMessage:
    content = '{"ok": true}'
    tool_calls = None


class _FakeOpenAIChoice:
    message = _FakeOpenAIMessage()
    finish_reason = "stop"


class _FakeOpenAIUsage:
    prompt_tokens = 11
    prompt_tokens_details = type("PromptDetails", (), {"cached_tokens": 4})()
    completion_tokens = 7
    total_tokens = 18


class _FakeOpenAIResponse:
    id = "chatcmpl_test"
    choices = [_FakeOpenAIChoice()]
    usage = _FakeOpenAIUsage()


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> _FakeOpenAIResponse:
        self.kwargs = kwargs
        return _FakeOpenAIResponse()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def test_openai_compatible_provider_preserves_developer_role() -> None:
    """OpenAI-compatible requests send stable developer guidance unchanged."""
    from restscope.llm import LLMMessage, LLMRequest
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    client = _FakeOpenAIClient()
    OpenAICompatibleProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="openai_compatible",
            model="gpt-test",
            messages=[
                LLMMessage(role="system", content="Harness contract"),
                LLMMessage(role="developer", content="Direct child descriptions"),
                LLMMessage(role="user", content="Task"),
            ],
        )
    )

    assert client.chat.completions.kwargs is not None
    assert client.chat.completions.kwargs["messages"][:3] == [
        {"role": "system", "content": "Harness contract"},
        {"role": "developer", "content": "Direct child descriptions"},
        {"role": "user", "content": "Task"},
    ]


class _FailingCompletions:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def create(self, **kwargs: object) -> None:
        del kwargs
        raise self.error


class _FailingOpenAIClient:
    def __init__(self, error: Exception) -> None:
        self.chat = SimpleNamespace(completions=_FailingCompletions(error))


def test_openai_compatible_provider_classifies_exhausted_503_without_leaking_body() -> None:
    """A busy provider becomes a stable capacity error after SDK retries end."""
    from restscope.llm import (
        LLMMessage,
        LLMRequest,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    provider_body = "secret provider response body"
    sdk_error = RuntimeError(provider_body)
    sdk_error.status_code = 503  # type: ignore[attr-defined]
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        client=_FailingOpenAIClient(sdk_error),
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        provider.invoke(
            LLMRequest(
                provider="openai_compatible",
                model="gpt-test",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )

    assert caught.value.code == "provider_unavailable"
    assert caught.value.status_code == 503
    assert caught.value.retry_limit == 3
    assert provider_body not in str(caught.value)
    assert caught.value.__cause__ is sdk_error


@pytest.mark.parametrize(
    "transport_error",
    [ConnectionError("connection refused"), TimeoutError("request timed out")],
)
def test_openai_compatible_provider_classifies_exhausted_transport_failure(
    transport_error: Exception,
) -> None:
    """Connection and timeout exhaustion use the same safe capacity result."""
    from restscope.llm import (
        LLMMessage,
        LLMRequest,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="test-key",
        client=_FailingOpenAIClient(transport_error),
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        provider.invoke(
            LLMRequest(
                provider="openai_compatible",
                model="gpt-test",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )

    assert caught.value.status_code is None
    assert caught.value.__cause__ is transport_error


@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 599])
def test_openai_compatible_provider_classifies_retryable_http_statuses(
    status_code: int,
) -> None:
    """Every HTTP status retried by the SDK shares one terminal result."""
    from restscope.llm import (
        LLMMessage,
        LLMRequest,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    sdk_error = RuntimeError("provider detail")
    sdk_error.status_code = status_code  # type: ignore[attr-defined]
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        client=_FailingOpenAIClient(sdk_error),
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        provider.invoke(
            LLMRequest(
                provider="openai_compatible",
                model="gpt-test",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )

    assert caught.value.status_code == status_code


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_openai_compatible_provider_preserves_nonretryable_http_failures(
    status_code: int,
) -> None:
    """Caller, auth, permission, route, and validation errors stay nontransient."""
    from restscope.llm import (
        LLMMessage,
        LLMRequest,
        ProviderInvokeError,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    sdk_error = RuntimeError("request rejected")
    sdk_error.status_code = status_code  # type: ignore[attr-defined]
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        client=_FailingOpenAIClient(sdk_error),
    )

    with pytest.raises(ProviderInvokeError) as caught:
        provider.invoke(
            LLMRequest(
                provider="openai_compatible",
                model="gpt-test",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )

    assert not isinstance(caught.value, ProviderUnavailableError)


def test_openai_compatible_provider_converts_schema_and_tools_without_network() -> None:
    """Scenario: verify that openai compatible provider converts schema and tools without network."""
    from restscope.llm import LLMMessage, LLMRequest, ToolSpec
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    fake_client = _FakeOpenAIClient()
    provider = OpenAICompatibleProvider(api_key="test-key", base_url="https://example.test/v1", client=fake_client)

    response = provider.invoke(
        LLMRequest(
            provider="openai_compatible",
            model="gpt-test",
            messages=[LLMMessage(role="user", content="return json")],
            response_format="json_schema",
            json_schema_name="SimpleResult",
            json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            tools=[
                ToolSpec(
                    name="artifact.read_summary",
                    description="Read summary",
                    kind="local_function",
                    input_schema={"type": "object", "properties": {"artifact_id": {"type": "string"}}},
                    strict=True,
                )
            ],
            tool_choice="auto",
        )
    )

    kwargs = fake_client.chat.completions.kwargs
    assert kwargs is not None
    assert kwargs["model"] == "gpt-test"
    assert kwargs["response_format"]["type"] == "json_schema"
    provider_tool_name = kwargs["tools"][0]["function"]["name"]
    assert "." not in provider_tool_name
    assert len(provider_tool_name) <= 64
    assert kwargs["tools"][0]["function"]["strict"] is True
    assert response.parsed_json == {"ok": True}
    assert response.provider_request_id == "chatcmpl_test"
    assert response.cached_input_tokens == 4


def test_openai_compatible_provider_serializes_assistant_tool_call_history() -> None:
    """Scenario: verify that openai compatible provider serializes assistant tool call history."""
    from restscope.llm import LLMMessage, LLMRequest, ToolCall
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    fake_client = _FakeOpenAIClient()
    provider = OpenAICompatibleProvider(api_key="test-key", client=fake_client)
    provider.invoke(
        LLMRequest(
            provider="openai_compatible",
            model="gpt-test",
            messages=[
                LLMMessage(role="user", content="investigate"),
                LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_search",
                            name="catalog.search",
                            arguments={"query": "userId"},
                        )
                    ],
                ),
                LLMMessage(
                    role="tool",
                    content='{"results": []}',
                    name="catalog.search",
                    tool_call_id="call_search",
                ),
            ],
        )
    )

    messages = fake_client.chat.completions.kwargs["messages"]
    history_tool_name = messages[1]["tool_calls"][0]["function"]["name"]
    assert "." not in history_tool_name
    assert messages[1]["tool_calls"] == [
        {
            "id": "call_search",
            "type": "function",
            "function": {
                "name": history_tool_name,
                "arguments": '{"query": "userId"}',
            },
        }
    ]
    assert messages[2] == {
        "role": "tool",
        "content": '{"results": []}',
        "tool_call_id": "call_search",
    }


def test_openai_compatible_provider_restores_internal_dotted_tool_name() -> None:
    """Scenario: verify that openai compatible provider restores internal dotted tool name."""
    from restscope.llm import LLMMessage, LLMRequest, ToolSpec
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    class EchoToolCompletions:
        def create(self, **kwargs):
            assert "strict" not in kwargs["tools"][0]["function"]
            provider_name = kwargs["tools"][0]["function"]["name"]
            message = SimpleNamespace(
                content=None,
                tool_calls=[
                    {
                        "id": "call_search",
                        "function": {"name": provider_name, "arguments": '{"query":"userId"}'},
                    }
                ],
            )
            return SimpleNamespace(
                id="chatcmpl_tool",
                choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
                usage=None,
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=EchoToolCompletions()),
    )
    response = OpenAICompatibleProvider(api_key="test-key", client=client).invoke(
        LLMRequest(
            provider="openai_compatible",
            model="gpt-test",
            messages=[LLMMessage(role="user", content="search")],
            tools=[
                ToolSpec(
                    name="catalog.search",
                    description="Search symbols",
                    kind="local_function",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            tool_choice="auto",
        )
    )

    assert response.tool_calls[0].name == "catalog.search"


def test_model_builder_uses_named_thinking_and_fast_configs(tmp_path: Path) -> None:
    """Each raw slot translates directly without semantic role selection."""
    from restscope.llm import build_llm_model_config
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_PROVIDER=fake",
                "THINK_MODEL=strong-model",
                "THINK_TEMPERATURE=0.1",
                "THINK_MAX_TOKENS=4096",
                "FAST_PROVIDER=fake",
                "FAST_MODEL=fast-model",
                "FAST_TEMPERATURE=0.2",
            ]
        ),
        encoding="utf-8",
    )
    config = RESTScopeConfig.from_environment(env_file)
    thinking = build_llm_model_config("thinking", config.llm.thinking)
    fast = build_llm_model_config("fast", config.llm.fast)

    assert thinking.name == "thinking"
    assert thinking.model == "strong-model"
    assert fast.name == "fast"
    assert fast.model == "fast-model"
    assert fast.provider == "fake"


def test_deepseek_profiles_accept_one_m_context_and_384k_output(tmp_path: Path) -> None:
    """The approved local DeepSeek capacities remain valid for both model slots."""
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "THINK_PROVIDER=deepseek",
                "THINK_MODEL=deepseek-v4-flash",
                "THINK_CONTEXT_WINDOW_TOKENS=1048576",
                "THINK_MAX_TOKENS=393216",
                "FAST_PROVIDER=deepseek",
                "FAST_MODEL=deepseek-v4-flash",
                "FAST_CONTEXT_WINDOW_TOKENS=1048576",
                "FAST_MAX_TOKENS=393216",
            )
        ),
        encoding="utf-8",
    )

    config = RESTScopeConfig.from_environment(env_file)

    assert config.llm.thinking.context_window_tokens == 1_048_576
    assert config.llm.thinking.max_tokens == 393_216
    assert config.llm.fast.context_window_tokens == 1_048_576
    assert config.llm.fast.max_tokens == 393_216


def test_model_config_exposes_separate_context_and_output_limits(
    tmp_path: Path,
) -> None:
    """Scenario: context capacity and completion capacity remain distinct."""
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "THINK_MODEL=think-model",
                "FAST_MODEL=fast-model",
                "THINK_CONTEXT_WINDOW_TOKENS=200000",
                "THINK_MAX_TOKENS=12000",
            )
        ),
        encoding="utf-8",
    )

    config = RESTScopeConfig.from_environment(env_file)

    assert config.llm.thinking.context_window_tokens == 200000
    assert config.llm.thinking.max_tokens == 12000
    assert config.llm.fast.context_window_tokens == 131072
    assert config.llm.fast.max_tokens == 8192


def test_model_config_rejects_output_limit_that_fills_context(
    tmp_path: Path,
) -> None:
    """Scenario: a model must retain room for prompt evidence."""
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "THINK_MODEL=think-model",
                "THINK_CONTEXT_WINDOW_TOKENS=4096",
                "THINK_MAX_TOKENS=4096",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="THINK_MAX_TOKENS must be smaller",
    ):
        RESTScopeConfig.from_environment(env_file)


def test_deepseek_config_defaults_reasoning_by_model_slot_and_registers_provider(
    tmp_path: Path,
) -> None:
    """Scenario: verify that deepseek config defaults reasoning by model slot and registers provider."""
    from restscope.llm import build_llm_model_config, build_llm_registry
    from restscope.llm.providers.deepseek import DeepSeekProvider
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_PROVIDER=deepseek",
                "THINK_MODEL=deepseek-v4-pro",
                "THINK_API_KEY=test-key",
            ]
        ),
        encoding="utf-8",
    )

    config = RESTScopeConfig.from_environment(env_file)
    thinking = build_llm_model_config("thinking", config.llm.thinking)
    fast = build_llm_model_config("fast", config.llm.fast)
    registry = build_llm_registry(config.llm)

    assert thinking.reasoning.mode == "enabled"
    assert fast.reasoning.mode == "disabled"
    assert registry.list_names() == ["deepseek"]
    assert isinstance(registry.get("deepseek"), DeepSeekProvider)
    assert registry.get("deepseek").base_url == "https://api.deepseek.com"


def test_deepseek_config_parses_explicit_reasoning_effort(tmp_path: Path) -> None:
    """Scenario: verify that deepseek config parses explicit reasoning effort."""
    from restscope.llm import build_llm_model_config
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_PROVIDER=deepseek",
                "THINK_MODEL=deepseek-v4-pro",
                "THINK_API_KEY=test-key",
                "THINK_REASONING_MODE=enabled",
                "THINK_REASONING_EFFORT=max",
                "FAST_PROVIDER=deepseek",
                "FAST_MODEL=deepseek-v4-flash",
                "FAST_REASONING_MODE=disabled",
            ]
        ),
        encoding="utf-8",
    )

    config = RESTScopeConfig.from_environment(env_file)
    thinking = build_llm_model_config("thinking", config.llm.thinking)
    fast = build_llm_model_config("fast", config.llm.fast)

    assert thinking.reasoning.effort == "max"
    assert fast.reasoning.mode == "disabled"
    assert fast.reasoning.effort is None


def test_output_validator_prefers_parsed_json_and_reports_errors() -> None:
    """Scenario: verify that output validator prefers parsed json and reports errors."""
    from restscope.llm import LLMResponse, OutputValidator

    validator = OutputValidator()
    valid = validator.validate(
        response=LLMResponse(
            provider="fake",
            model="fake-model",
            content="not json",
            parsed_json={
                "campaign_type": "risk_targeted_fuzzing",
                "target_operation_ids": ["op_1"],
                "hypothesis": "POST /pets may fail",
                "rationale": "High risk operation",
            },
        ),
        output_model=CampaignSpec,
    )

    invalid = validator.validate(
        response=LLMResponse(provider="fake", model="fake-model", content='{"campaign_type": "only"}'),
        output_model=CampaignSpec,
    )

    assert valid.valid is True
    assert valid.validated_object is not None
    assert valid.validated_object.campaign_type == "risk_targeted_fuzzing"
    assert invalid.valid is False
    assert invalid.errors


def test_agent_toolbox_exposes_and_executes_only_explicit_tools(tool_context) -> None:
    """An Agent's toolbox is its complete availability decision."""
    del tool_context
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall, ToolSpec

    toolbox = AgentToolbox()
    toolbox.register(
        spec=ToolSpec(
            name="artifact.read_summary",
            description="Read summary",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
                "additionalProperties": False,
            },
        ),
        execute=lambda artifact_id: {
            "content": f"summary:{artifact_id}",
            "structured": {"artifact_id": artifact_id},
        },
    )

    success = toolbox.execute(
        ToolCall(id="call_1", name="artifact.read_summary", arguments={"artifact_id": "artifact_1"})
    )
    denied = toolbox.execute(
        ToolCall(id="call_2", name="external.run_campaign", arguments={})
    )

    assert [tool.name for tool in toolbox.specs()] == ["artifact.read_summary"]
    assert success.status == "succeeded"
    assert success.content == "summary:artifact_1"
    assert denied.status == "denied"
    assert denied.error is not None


def test_redactor_only_removes_registered_secret_values() -> None:
    """Scenario: verify that redactor only removes registered secret values."""
    from restscope.observability import Redactor

    text = "Authorization: Bearer abc.def.ghi api_key=secret123 access_token: token-value"
    redacted = Redactor(["secret123"]).redact_text(text)

    assert "abc.def.ghi" in redacted
    assert "secret123" not in redacted
    assert "token-value" in redacted
    assert "***REDACTED***" in redacted
