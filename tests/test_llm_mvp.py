from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel


class CampaignSpec(BaseModel):
    campaign_type: str
    target_operation_ids: list[str]
    hypothesis: str
    rationale: str


def test_llm_schema_serialization_and_import_smoke() -> None:
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


def test_operation_smoke_roles_use_the_shared_fast_model() -> None:
    from restscope.llm import LLMModelConfig, ModelSelector

    selector = ModelSelector(
        thinking=LLMModelConfig(
            role="thinking",
            provider="stub",
            model="thinking-model",
        ),
        fast=LLMModelConfig(
            role="fast",
            provider="stub",
            model="fast-model",
        ),
    )

    assert selector.select("operation_smoke_parameter_diagnosis").model == "fast-model"
    assert selector.select("operation_smoke_generator_patch").model == "fast-model"


def test_reasoning_config_and_tool_provider_context_round_trip() -> None:
    from restscope.llm import LLMMessage, LLMReasoningConfig, LLMRequest, ToolCall

    request = LLMRequest(
        provider="deepseek",
        model="deepseek-v4-pro",
        messages=[LLMMessage(role="user", content="investigate")],
        reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
    )
    tool_call = ToolCall(
        id="call_search",
        name="openapi.search_symbols",
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


def test_llm_client_invokes_provider_once_and_propagates_failure() -> None:
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


def test_tool_call_does_not_retain_raw_provider_payload() -> None:
    from restscope.llm import ToolCall

    tool_call = ToolCall(id="call_1", name="openapi.inspect")

    assert "raw" not in type(tool_call).model_fields


class _FakeOpenAIMessage:
    content = '{"ok": true}'
    tool_calls = None


class _FakeOpenAIChoice:
    message = _FakeOpenAIMessage()
    finish_reason = "stop"


class _FakeOpenAIUsage:
    prompt_tokens = 11
    completion_tokens = 7
    total_tokens = 18


class _FakeOpenAIResponse:
    id = "chatcmpl_test"
    choices = [_FakeOpenAIChoice()]
    usage = _FakeOpenAIUsage()


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeOpenAIResponse:
        self.kwargs = kwargs
        return _FakeOpenAIResponse()


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


def test_openai_compatible_provider_converts_schema_and_tools_without_network() -> None:
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
    assert response.parsed_json == {"ok": True}
    assert response.provider_request_id == "chatcmpl_test"


def test_openai_compatible_provider_serializes_assistant_tool_call_history() -> None:
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
                            name="openapi.search_symbols",
                            arguments={"query": "userId"},
                        )
                    ],
                ),
                LLMMessage(
                    role="tool",
                    content='{"results": []}',
                    name="openapi.search_symbols",
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
    from restscope.llm import LLMMessage, LLMRequest, ToolSpec
    from restscope.llm.providers.openai_compatible import OpenAICompatibleProvider

    class EchoToolCompletions:
        def create(self, **kwargs):
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
                    name="openapi.search_symbols",
                    description="Search symbols",
                    kind="local_function",
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            tool_choice="auto",
        )
    )

    assert response.tool_calls[0].name == "openapi.search_symbols"


def test_model_selector_uses_thinking_and_fast_configs(tmp_path: Path) -> None:
    from restscope.llm import ModelSelector
    from restscope.restscope_config import RESTScopeConfig

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
    selector = ModelSelector.from_config(config.llm)

    assert selector.select("planner").model == "strong-model"
    assert selector.select("result_analyst").model == "strong-model"
    assert selector.select("check_designer").model == "strong-model"
    assert selector.select("intelligence_updater").model == "strong-model"
    assert selector.select("decision_maker").model == "fast-model"
    assert selector.select("decision_maker").provider == "fake"


def test_deepseek_config_defaults_reasoning_by_model_slot_and_registers_provider(
    tmp_path: Path,
) -> None:
    from restscope.llm import ModelSelector, build_llm_registry
    from restscope.llm.providers.deepseek import DeepSeekProvider
    from restscope.restscope_config import RESTScopeConfig

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
    selector = ModelSelector.from_config(config.llm)
    registry = build_llm_registry(config.llm)

    assert selector.select("openapi_retrieval").reasoning.mode == "enabled"
    assert selector.select("decision_maker").reasoning.mode == "disabled"
    assert registry.list_names() == ["deepseek"]
    assert isinstance(registry.get("deepseek"), DeepSeekProvider)
    assert registry.get("deepseek").base_url == "https://api.deepseek.com"


def test_deepseek_config_parses_explicit_reasoning_effort(tmp_path: Path) -> None:
    from restscope.llm import ModelSelector
    from restscope.restscope_config import RESTScopeConfig

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

    selector = ModelSelector.from_config(RESTScopeConfig.from_environment(env_file).llm)

    assert selector.select("openapi_retrieval").reasoning.effort == "max"
    assert selector.select("decision_maker").reasoning.mode == "disabled"
    assert selector.select("decision_maker").reasoning.effort is None


def test_output_validator_prefers_parsed_json_and_reports_errors() -> None:
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


def test_tool_runtime_selects_allows_denies_and_executes_read_only_tools(tool_context) -> None:
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry, ToolSelector
    from restscope.llm import ToolCall, ToolSpec

    registry = ToolRegistry()
    registry.register(
        spec=ToolSpec(
            name="artifact.read_summary",
            description="Read summary",
            kind="local_function",
            input_schema={"type": "object"},
            read_only=True,
        ),
        handler=lambda _context, /, artifact_id: {
            "content": f"summary:{artifact_id}",
            "structured": {"artifact_id": artifact_id},
        },
    )
    registry.register(
        spec=ToolSpec(
            name="schemathesis.run_campaign",
            description="Run campaign",
            kind="local_function",
            input_schema={"type": "object"},
            read_only=False,
            requires_approval=True,
            risk_level="high",
        ),
        handler=lambda _context, /: {"content": "should not run"},
    )

    selector = ToolSelector(registry)
    selected = selector.select_for_role(role="planner", state={})
    validator = ToolCallValidator(registry, ToolPolicy())
    executor = ToolExecutor(registry, validator)
    executor.bind_context(tool_context)

    success = executor.execute(
        tool_call=ToolCall(id="call_1", name="artifact.read_summary", arguments={"artifact_id": "artifact_1"}),
        role="planner",
        state={},
    )
    denied = executor.execute(
        tool_call=ToolCall(id="call_2", name="schemathesis.run_campaign", arguments={}),
        role="planner",
        state={},
    )

    assert [tool.name for tool in selected] == ["artifact.read_summary"]
    assert success.status == "succeeded"
    assert success.content == "summary:artifact_1"
    assert denied.status == "denied"
    assert denied.error is not None


def test_redactor_only_removes_registered_secret_values() -> None:
    from restscope.redaction import Redactor

    text = "Authorization: Bearer abc.def.ghi api_key=secret123 access_token: token-value"
    redacted = Redactor(["secret123"]).redact_text(text)

    assert "abc.def.ghi" in redacted
    assert "secret123" not in redacted
    assert "token-value" in redacted
    assert "***REDACTED***" in redacted
