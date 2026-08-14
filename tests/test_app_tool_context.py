"""Regression scenarios for app tool context. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json

import pytest

from tests.agent_helpers import start_test_agent


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


def _app_and_resources(monkeypatch, tmp_path):
    """Return an App with minimal private resources for lifecycle scenarios."""
    from restscope import RESTScopeApp
    from restscope.config import RESTScopeConfig
    from restscope.observability import TracingRuntime

    class Resources:
        """Implement only the private resource operations consumed by runtime."""

        tracing = TracingRuntime.disabled()
        run_observer = None
        ui_url = None

        def __init__(self) -> None:
            self.context = None
            self.started = 0
            self.closed = False

        def bind_target(self, context) -> None:
            self.context = context

        def run_orchestration(self, focus: str | None = None) -> None:
            self.started += 1
            self.focus = focus

        def close(self) -> None:
            self.context = None
            self.closed = True

    resources = Resources()
    monkeypatch.setattr(
        "restscope.app.runtime._compose_app_resources",
        lambda _config: resources,
    )
    return RESTScopeApp(RESTScopeConfig.from_environment(tmp_path / ".env")), resources


def _app(monkeypatch, tmp_path):
    """Build one lifecycle App when no resource inspection is needed."""
    app, _resources = _app_and_resources(monkeypatch, tmp_path)
    return app


def test_production_profiles_separate_planning_from_task_execution(
    monkeypatch,
    tmp_path,
) -> None:
    """Orchestrator sees only progress; each Executor may use one Patch child."""
    from restscope.app.profiles import _build_agent_runtime_definition
    from restscope.config import RESTScopeConfig
    from restscope.harness import ContextSourceBinding
    from restscope.llm import LLMClient, LLMResponse
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.observability import TracingRuntime

    class Provider:
        """Record the one production-shaped Main request without network I/O."""

        name = "openai_compatible"

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
    monkeypatch.setattr(
        "restscope.app.profiles.build_llm_client",
        lambda *_args, **_kwargs: client,
    )
    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.openai_compatible]\n"
        'api_key_env = "TEST_MODEL_API_KEY"\n'
        "\n"
        "[models.default]\n"
        'provider = "openai_compatible"\n'
        'model = "thinking-model"\n'
        "max_tokens = 512\n"
        "context_tokens = 8192\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nTEST_MODEL_API_KEY=test-key\n",
        encoding="utf-8",
    )
    definition = _build_agent_runtime_definition(
        RESTScopeConfig.from_environment(env_file),
        tracing_runtime=TracingRuntime.disabled(),
        test_progress_context=ContextSourceBinding(
            name="test-progress",
            read=lambda: "current progress",
        ),
    )
    assert definition is not None
    orchestrator = definition.profiles[0]
    profile = definition.profiles[1]

    assert orchestrator.name == "orchestrator"
    assert orchestrator.tool_names == ("database.query", "file.read")
    assert orchestrator.skill_names == ("query-restscope-database",)
    assert orchestrator.subagent_profile_names == ()
    assert orchestrator.context_sources == ("test-progress",)
    assert [item.name for item in definition.context_sources] == ["test-progress"]
    assert profile.name == "task-executor"
    assert profile.model_config_name == "default"
    assert orchestrator.reasoning_effort == "high"
    assert profile.reasoning_effort == "high"
    assert profile.tool_names == (
        "plan.read",
        "plan.update",
        "openapi.list_operations",
        "openapi.list_inputs",
        "openapi.list_response_fields",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "request_generation.get_input_state",
        "test_case.run_batch",
        "test_case.get_batch_results",
        "test_case.get",
        "restscope.http.request",
        "subagent.start",
        "subagent.wait",
        "subagent.cancel",
        "database.query",
        "file.read",
    )
    assert profile.skill_names == (
        "resolve-operation-failures",
        "query-restscope-database",
    )
    assert profile.context_sources == ()
    assert profile.subagent_profile_names == ("parameter-patch",)
    patch_profile = next(
        item for item in definition.profiles if item.name == "parameter-patch"
    )
    assert patch_profile.skill_names == ("apply-parameter-patch",)
    assert patch_profile.model_config_name == "default"
    assert patch_profile.reasoning_effort == "low"
    assert "parameter_patch.apply" in patch_profile.tool_names
    assert "database.query" not in patch_profile.tool_names
    assert "query-restscope-database" not in patch_profile.skill_names
    for narrow_profile_name in (
        "resource-identifier-selector",
        "resource-state-selector",
    ):
        narrow_profile = next(
            item for item in definition.profiles if item.name == narrow_profile_name
        )
        assert narrow_profile.tool_names == ()
        assert narrow_profile.skill_names == ()
        assert narrow_profile.model_config_name == "default"
        assert narrow_profile.reasoning_effort == "none"

    assert "Prioritize operations" in orchestrator.instructions
    assert "reproducible happy-path" in orchestrator.instructions
    assert "`happy_path`" in orchestrator.instructions
    assert "`exceptional`" in orchestrator.instructions
    assert (
        "`completed`, `partial`, `blocked`, or lifecycle failure"
        in orchestrator.instructions
    )
    assert "`bug_found`" in orchestrator.instructions
    assert "`unknown` or `not_met`" in orchestrator.instructions
    assert "Do not choose the next Operation" in profile.instructions
    assert "return `blocked` rather than expanding" in profile.instructions
    assert "`resolve-operation-failures`" in profile.instructions
    assert "`bug_found`" in profile.instructions
    assert {item.profile_name for item in definition.system_agents}.issuperset(
        {
            "orchestrator",
            "task-executor",
        }
    )


def test_harness_binds_new_domain_tools_without_granting_them_to_main(
    api_behavior_catalog,
) -> None:
    """Binding a domain Tool grants it only to a Profile that names it."""
    from restscope.agent import AgentProfile
    from restscope.db import create_engine_from_url
    from restscope.harness import AgentRuntimeDefinition, build_harness
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
    )
    from restscope.tools.database import DatabaseQueryToolBackend
    from restscope.tools.test_case import TestCaseQueryToolBackend

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
        reasoning_effort="none",
        tool_names=(
            "openapi.list_operations",
            "request_generation.get_input_state",
            "request_generation.validate_patch",
            "parameter_patch.apply",
            "test_case.run_batch",
            "test_case.get_batch_results",
            "test_case.get",
            "database.query",
        ),
    )
    runtime = build_harness(
        request_generation_patch_runtime=RequestGenerationPatchRuntime(
            store=store,
            ir_provider=lambda: None,
        ),
        operation_testing_service=OperationTestingService(
            config_store=store,
            api_behavior_catalog=api_behavior_catalog,
        ),
        test_case_query_backend=TestCaseQueryToolBackend(catalog=api_behavior_catalog),
        database_query_backend=DatabaseQueryToolBackend(
            engine=create_engine_from_url("sqlite:///:memory:")
        ),
        agent_runtime=AgentRuntimeDefinition(
            profiles=(profile,),
            models=(
                LLMModelConfig(
                    name="thinking",
                    provider="unused",
                    model="unused",
                    max_tokens=128,
                    context_window_tokens=4_096,
                ),
            ),
            client=LLMClient(registry),
        ),
    )

    agent = start_test_agent(runtime, "binding-check")
    assert tuple(tool.name for tool in agent.toolbox.specs()) == profile.tool_names
    agent.close()


def test_app_initializes_once_and_starts_one_blocking_orchestration(
    monkeypatch, tmp_path
) -> None:
    """One parsed target and optional focus feed the sole long-task loop."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.tools.context import ToolContextError

    original_parse = OpenAPIParser.parse
    seen: list[object] = []

    def counting_parse(source):
        seen.append(source)
        return original_parse(source)

    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(counting_parse))
    app, resources = _app_and_resources(monkeypatch, tmp_path)

    headers = {"Authorization": "Bearer runtime-secret"}
    source = {"kind": "inline", "format": "json", "content": json.dumps(_spec())}

    result = app.initialize(
        schema_source=source,
        base_url="https://api.example.test",
        headers=headers,
    )
    source["content"] = "changed"
    headers["Authorization"] = "changed"

    assert app.start("Prefer read-only operations.") is None

    assert len(seen) == 1
    assert result is None
    assert resources.context.baseline_schema_source["content"] != "changed"
    assert resources.context.headers["Authorization"] == "Bearer runtime-secret"
    assert resources.focus == "Prefer read-only operations."
    assert "runtime-secret" not in repr(resources.context)

    with pytest.raises(ToolContextError) as exc_info:
        app.initialize(
            schema_source={"kind": "inline", "content": json.dumps(_spec())},
            base_url="https://api.example.test",
        )
    assert exc_info.value.code == "tool_context_already_initialized"
    with pytest.raises(RuntimeError, match="already started"):
        app.start()


@pytest.mark.parametrize(
    ("schema_source", "parser_input"),
    [
        ({"kind": "file", "path": "/tmp/openapi.yaml"}, "/tmp/openapi.yaml"),
        (
            {"kind": "url", "url": "https://example.test/openapi.yaml"},
            "https://example.test/openapi.yaml",
        ),
        (
            {"kind": "inline", "format": "yaml", "content": "openapi: 3.0.3"},
            "openapi: 3.0.3",
        ),
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
    monkeypatch.setattr(
        OpenAPIParser,
        "parse",
        staticmethod(lambda source: seen.append(source) or parsed),
    )
    app, resources = _app_and_resources(monkeypatch, tmp_path)

    result = app.initialize(schema_source=schema_source, base_url="https://api.test")

    assert result is None
    assert seen == [parser_input]
    assert dict(resources.context.baseline_schema_source) == schema_source


def test_app_allows_retry_after_initialization_failure(monkeypatch, tmp_path) -> None:
    """Scenario: verify that app allows retry after initialization failure."""
    from restscope.openapi_parser import OpenAPIParser

    parsed = OpenAPIParser.parse(_spec())
    attempts = iter([ValueError("broken schema"), parsed])

    def parse(_source):
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(parse))
    app, resources = _app_and_resources(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="broken schema"):
        app.initialize(
            schema_source={"kind": "inline", "content": "broken"},
            base_url="https://api.test",
        )
    assert resources.context is None

    app.initialize(
        schema_source={"kind": "inline", "content": "valid"},
        base_url="https://api.test",
    )
    assert resources.context.ir is parsed


def test_app_rejects_an_openapi_schema_without_operations_and_remains_retryable(
    monkeypatch, tmp_path
) -> None:
    """Scenario: verify that app rejects an openapi schema without operations and remains retryable."""
    app, resources = _app_and_resources(monkeypatch, tmp_path)
    empty = {
        "openapi": "3.0.3",
        "info": {"title": "Empty", "version": "1.0"},
        "paths": {},
    }

    with pytest.raises(ValueError, match="no testable operations"):
        app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(empty),
            },
            base_url="https://api.test",
        )

    assert resources.context is None
    assert (
        app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(_spec()),
            },
            base_url="https://api.test",
        )
        is None
    )
    assert resources.context.ir.operations


def test_app_requires_initialization_and_clears_context_on_close(
    monkeypatch, tmp_path
) -> None:
    """Scenario: verify that app requires initialization and clears context on close."""
    from restscope.tools.context import ToolContextError

    app, resources = _app_and_resources(monkeypatch, tmp_path)

    with pytest.raises(ToolContextError) as exc_info:
        app.start()
    assert exc_info.value.code == "tool_context_not_initialized"

    app.initialize(
        schema_source={
            "kind": "inline",
            "format": "json",
            "content": json.dumps(_spec()),
        },
        base_url="https://api.test",
    )
    assert resources.context is not None

    app.close()

    assert resources.context is None
    assert resources.closed is True
    with pytest.raises(RuntimeError, match="closed"):
        app.start()
