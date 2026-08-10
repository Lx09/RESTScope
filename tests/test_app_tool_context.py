"""Regression scenarios for app tool context. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json

import pytest


def _spec(*, operation_id: str = "listPets") -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "App Context", "version": "1.0"},
        "paths": {
            "/pets": {
                "get": {
                    "operationId": operation_id,
                    "parameters": [
                        {"name": "cursor", "in": "query", "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def _app(tmp_path):
    from restscope.agent import AgentProfile
    from restscope import RESTScopeApp
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.llm import LLMClient, LLMModelConfig, LLMResponse
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.config import RESTScopeConfig

    class Provider:
        """Complete the blocking Main loop locally without external I/O."""

        name = "scripted"

        def invoke(self, request):
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json={"summary": "Main loop complete.", "findings": []},
            )

    database = tmp_path / "app-context.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_URL=sqlite:///{database}\n", encoding="utf-8")
    registry = LLMProviderRegistry()
    registry.register(Provider())
    runtime = build_harness(
        agent_runtime=AgentRuntimeDefinition(
            profiles=(AgentProfile(name="main", model_config_name="thinking"),),
            models=(
                LLMModelConfig(
                    role="thinking",
                    provider="scripted",
                    model="thinking-model",
                    max_tokens=512,
                    context_window_tokens=8_192,
                ),
            ),
            client=LLMClient(registry),
        )
    )
    return RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        harness_runtime=runtime,
    )


def test_production_main_profile_is_thinking_and_capability_light(
    monkeypatch,
    tmp_path,
) -> None:
    """Unimplemented testing methods stay absent from the first Main Profile."""
    from restscope.app import _build_main_agent_runtime_definition
    from restscope.harness import build_harness
    from restscope.llm import LLMClient, LLMResponse
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.observability import TracingRuntime
    from restscope.config import RESTScopeConfig

    class Provider:
        """Record the one production-shaped Main request without network I/O."""

        name = "scripted"

        def __init__(self):
            self.requests = []

        def invoke(self, request):
            self.requests.append(request)
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json={"summary": "Capabilities reported.", "findings": []},
            )

    provider = Provider()
    registry = LLMProviderRegistry()
    registry.register(provider)
    client = LLMClient(registry)
    monkeypatch.setattr("restscope.app.build_llm_client", lambda *_args, **_kwargs: client)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "THINK_PROVIDER=scripted\n"
        "THINK_MODEL=thinking-model\n"
        "THINK_MAX_TOKENS=512\n"
        "THINK_CONTEXT_WINDOW_TOKENS=8192\n",
        encoding="utf-8",
    )
    definition = _build_main_agent_runtime_definition(
        RESTScopeConfig.from_environment(env_file),
        tracing_runtime=TracingRuntime.disabled(),
    )
    assert definition is not None
    profile = definition.profiles[0]

    assert profile.name == "main"
    assert profile.model_config_name == "thinking"
    assert profile.tool_names == ("plan.read", "plan.update")
    assert profile.skill_names == ()
    assert profile.context_sources == ()
    assert profile.subagent_profile_names == ()

    build_harness(agent_runtime=definition).start_main_agent("main").start()
    request = provider.requests[0]
    assert request.model == "thinking-model"
    assert [tool.name for tool in request.tools] == ["plan.read", "plan.update"]
    assert any(
        "single long-lived Main Agent" in message.content
        for message in request.messages
    )


def test_harness_binds_new_domain_tools_without_granting_them_to_main() -> None:
    """A caller Profile can resolve every new binding, while production Main stays unchanged."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.request_generation import RequestGenerationConfigStore

    class UnusedProvider:
        """Satisfy Profile validation; this binding test never invokes a model."""

        name = "unused"

        def invoke(self, _request):
            raise AssertionError("Model invocation is outside this test")

    store = RequestGenerationConfigStore()
    registry = LLMProviderRegistry()
    registry.register(UnusedProvider())
    profile = AgentProfile(
        name="binding-check",
        model_config_name="thinking",
        tool_names=(
            "openapi.list_operations",
            "request_generation.get_input_state",
            "request_generation.validate_patch",
            "parameter_patch.apply",
            "test_case.run_batch",
        ),
    )
    runtime = build_harness(
        request_generation_store=store,
        operation_testing_service=OperationTestingService(config_store=store),
        agent_runtime=AgentRuntimeDefinition(
            profiles=(profile,),
            models=(
                LLMModelConfig(
                    role="thinking",
                    provider="unused",
                    model="unused",
                    max_tokens=128,
                    context_window_tokens=4_096,
                ),
            ),
            client=LLMClient(registry),
        ),
    )

    agent = runtime.start_main_agent("binding-check")
    assert tuple(tool.name for tool in agent.toolbox.specs()) == profile.tool_names
    agent.close()


def test_app_initializes_once_and_starts_one_blocking_main_loop(monkeypatch, tmp_path) -> None:
    """One parsed target feeds the only taskless Main loop in this App."""
    from restscope.tools.context import ToolContextError
    from restscope.openapi_parser import OpenAPIParser

    original_parse = OpenAPIParser.parse
    seen: list[object] = []

    def counting_parse(source):
        seen.append(source)
        return original_parse(source)

    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(counting_parse))
    app = _app(tmp_path)

    headers = {"Authorization": "Bearer runtime-secret"}
    source = {"kind": "inline", "format": "json", "content": json.dumps(_spec())}

    context = app.initialize(
        schema_source=source,
        base_url="https://api.example.test",
        headers=headers,
    )
    source["content"] = "changed"
    headers["Authorization"] = "changed"

    assert app.start() is None

    assert len(seen) == 1
    assert app.tool_context is context
    assert app.harness_runtime.require_context() is context
    assert context.baseline_schema_source["content"] != "changed"
    assert context.headers["Authorization"] == "Bearer runtime-secret"
    assert "runtime-secret" not in repr(context)

    with pytest.raises(ToolContextError) as exc_info:
        app.initialize(schema_source={"kind": "inline", "content": json.dumps(_spec())})
    assert exc_info.value.code == "tool_context_already_initialized"
    with pytest.raises(RuntimeError, match="already started"):
        app.start()


@pytest.mark.parametrize(
    ("schema_source", "parser_input"),
    [
        ({"kind": "file", "path": "/tmp/openapi.yaml"}, "/tmp/openapi.yaml"),
        ({"kind": "url", "url": "https://example.test/openapi.yaml"}, "https://example.test/openapi.yaml"),
        ({"kind": "inline", "format": "yaml", "content": "openapi: 3.0.3"}, "openapi: 3.0.3"),
    ],
)
def test_app_validates_and_forwards_supported_schema_sources(
    monkeypatch,
    schema_source,
    parser_input,
    tmp_path,
) -> None:
    """Scenario: verify that app validates and forwards supported schema sources."""
    from restscope.openapi_parser import OpenAPIParser

    parsed = OpenAPIParser.parse(_spec())
    seen = []
    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(lambda source: seen.append(source) or parsed))
    app = _app(tmp_path)

    context = app.initialize(schema_source=schema_source)

    assert seen == [parser_input]
    assert dict(context.baseline_schema_source) == schema_source


def test_app_allows_retry_after_initialization_failure(monkeypatch, tmp_path) -> None:
    """Scenario: verify that app allows retry after initialization failure."""
    from restscope.tools.context import ToolContextError
    from restscope.openapi_parser import OpenAPIParser

    parsed = OpenAPIParser.parse(_spec())
    attempts = iter([ValueError("broken schema"), parsed])

    def parse(_source):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(parse))
    app = _app(tmp_path)

    with pytest.raises(ValueError, match="broken schema"):
        app.initialize(schema_source={"kind": "inline", "content": "broken"})
    assert app.tool_context is None
    with pytest.raises(ToolContextError) as exc_info:
        app.harness_runtime.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"

    context = app.initialize(schema_source={"kind": "inline", "content": "valid"})
    assert context.ir is parsed


def test_app_rejects_an_openapi_schema_without_operations_and_remains_retryable(tmp_path) -> None:
    """Scenario: verify that app rejects an openapi schema without operations and remains retryable."""
    app = _app(tmp_path)
    empty = {
        "openapi": "3.0.3",
        "info": {"title": "Empty", "version": "1.0"},
        "paths": {},
    }

    with pytest.raises(ValueError, match="no testable operations"):
        app.initialize(
            schema_source={"kind": "inline", "format": "json", "content": json.dumps(empty)}
        )

    assert app.tool_context is None
    assert app.initialize(
        schema_source={"kind": "inline", "format": "json", "content": json.dumps(_spec())}
    ).ir.operations


def test_app_requires_initialization_and_clears_context_on_close(tmp_path) -> None:
    """Scenario: verify that app requires initialization and clears context on close."""
    from restscope.tools.context import ToolContextError

    app = _app(tmp_path)
    assert app.harness_runtime is not None

    with pytest.raises(ToolContextError) as exc_info:
        app.start()
    assert exc_info.value.code == "tool_context_not_initialized"

    context = app.initialize(
        schema_source={"kind": "inline", "format": "json", "content": json.dumps(_spec())}
    )
    runtime = app.harness_runtime
    assert runtime.require_context() is context

    app.close()

    assert app.tool_context is None
    with pytest.raises(ToolContextError) as exc_info:
        runtime.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"
    with pytest.raises(RuntimeError, match="closed"):
        app.start()
