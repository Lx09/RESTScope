from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _context_package():
    from restscope.context import ContextMessage, ContextPackage, OutputContract

    return ContextPackage(
        id="context_1",
        task_id="task_1",
        schema_id="schema_1",
        role="planner",
        cycle_index=3,
        prompt_version="planner_v1",
        sections=[],
        messages=[
            ContextMessage(role="system", content="You are the planner."),
            ContextMessage(role="user", content="Return a campaign spec."),
        ],
        output_contract=OutputContract(
            name="TestCampaignSpec",
            description="Planner contract",
            json_schema={
                "type": "object",
                "required": ["campaign_type", "target_operation_ids", "hypothesis", "rationale"],
                "properties": {
                    "campaign_type": {"type": "string"},
                    "target_operation_ids": {"type": "array", "items": {"type": "string"}},
                    "hypothesis": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        ),
        source_refs={"operations": ["op_1"]},
        token_budget=2000,
        metadata={"context_snapshot_id": "ctx_1"},
    )


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


def test_fake_provider_returns_contract_specific_json_and_tool_calls() -> None:
    from restscope.llm import LLMMessage, LLMRequest, ToolSpec
    from restscope.llm.providers.fake import FakeProvider

    provider = FakeProvider()
    response = provider.invoke(
        LLMRequest(
            provider="fake",
            model="fake-model",
            messages=[LLMMessage(role="user", content="plan")],
            response_format="json_schema",
            json_schema_name="TestCampaignSpec",
        )
    )

    assert response.provider == "fake"
    assert response.parsed_json is not None
    assert response.parsed_json["campaign_type"] == "risk_targeted_fuzzing"
    assert response.parsed_json["target_operation_ids"]

    tool_response = provider.invoke(
        LLMRequest(
            provider="fake",
            model="fake-model",
            messages=[LLMMessage(role="user", content="need tool")],
            tools=[
                ToolSpec(
                    name="artifact.read_summary",
                    description="Read summary",
                    kind="local_function",
                    input_schema={"type": "object"},
                )
            ],
            tool_choice="required",
        )
    )

    assert tool_response.tool_calls[0].name == "artifact.read_summary"


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
    assert kwargs["tools"][0]["function"]["name"] == "artifact.read_summary"
    assert response.parsed_json == {"ok": True}
    assert response.provider_request_id == "chatcmpl_test"


def test_request_factory_preserves_context_contract_metadata_and_tools() -> None:
    from restscope.llm import LLMModelConfig, LLMRequestFactory, ToolSpec

    context = _context_package()
    request = LLMRequestFactory().from_context(
        context_package=context,
        model_config=LLMModelConfig(
            role="planner",
            provider="fake",
            model="fake-model",
            tool_choice="auto",
        ),
        tools=[
            ToolSpec(
                name="artifact.read_summary",
                description="Read summary",
                kind="local_function",
                input_schema={"type": "object"},
            )
        ],
    )

    assert [message.role for message in request.messages] == ["system", "user"]
    assert request.json_schema_name == "TestCampaignSpec"
    assert request.json_schema == context.output_contract.json_schema
    assert request.metadata["task_id"] == "task_1"
    assert request.metadata["context_snapshot_id"] == "ctx_1"
    assert request.tool_choice == "auto"


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


def test_output_validator_prefers_parsed_json_and_reports_errors() -> None:
    from restscope.llm import LLMResponse, OutputValidator

    validator = OutputValidator()
    context = _context_package()
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
        context_package=context,
    )

    invalid = validator.validate(
        response=LLMResponse(provider="fake", model="fake-model", content='{"campaign_type": "only"}'),
        output_model=CampaignSpec,
        context_package=context,
    )

    assert valid.valid is True
    assert valid.validated_object is not None
    assert valid.validated_object.campaign_type == "risk_targeted_fuzzing"
    assert invalid.valid is False
    assert invalid.errors


def test_tool_runtime_selects_allows_denies_and_executes_read_only_tools() -> None:
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
        handler=lambda artifact_id: {"content": f"summary:{artifact_id}", "structured": {"artifact_id": artifact_id}},
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
        handler=lambda: {"content": "should not run"},
    )

    selector = ToolSelector(registry)
    selected = selector.select_for_role(role="planner", state={})
    validator = ToolCallValidator(registry, ToolPolicy())
    executor = ToolExecutor(registry, validator)

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


def test_redactor_removes_common_secret_patterns() -> None:
    from restscope.llm import Redactor

    text = "Authorization: Bearer abc.def.ghi api_key=secret123 access_token: token-value"
    redacted = Redactor().redact_text(text)

    assert "abc.def.ghi" not in redacted
    assert "secret123" not in redacted
    assert "token-value" not in redacted
    assert "***REDACTED***" in redacted
