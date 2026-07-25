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
    from restscope import RESTScopeApp
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_stub import PassingOperationSmokeAgent

    database = tmp_path / "app-context.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_URL=sqlite:///{database}\n", encoding="utf-8")
    return RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_agent=PassingOperationSmokeAgent(),
    )


def _request():
    from restscope.agent import RESTScopeRunRequest

    return RESTScopeRunRequest()


def test_app_initializes_once_and_reuses_the_same_ir_across_runs(monkeypatch, tmp_path) -> None:
    from restscope.agent.openapi_retrieval import OpenAPIRetrievalAgent, register_openapi_retrieval_tool
    from restscope.capabilities import ToolContextError
    from restscope.llm import LLMModelConfig, LLMResponse, ToolCall
    from restscope.openapi_parser import OpenAPIParser

    original_parse = OpenAPIParser.parse
    seen: list[object] = []

    def counting_parse(source):
        seen.append(source)
        return original_parse(source)

    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(counting_parse))
    app = _app(tmp_path)

    class NotFoundClient:
        def __init__(self):
            self.requests = []

        def invoke(self, request):
            self.requests.append(request)
            return LLMResponse(
                provider="fake",
                model=request.model,
                parsed_json={
                    "status": "not_found",
                    "candidates": [],
                    "conflicts": [],
                    "evidence_sufficient": True,
                    "limitations": [],
                    "warnings": [],
                },
            )

    client = NotFoundClient()
    retrieval_spec = register_openapi_retrieval_tool(
        app.capability_runtime.tool_registry,
        OpenAPIRetrievalAgent(
            client=client,
            model=LLMModelConfig(role="openapi_retrieval", provider="fake", model="thinking-test"),
        ),
    )
    headers = {"Authorization": "Bearer runtime-secret"}
    source = {"kind": "inline", "format": "json", "content": json.dumps(_spec())}

    context = app.initialize(
        schema_source=source,
        base_url="https://api.example.test",
        headers=headers,
    )
    source["content"] = "changed"
    headers["Authorization"] = "changed"

    first = app.run(_request())
    second = app.run(_request())
    retrieval_arguments = {
        "query": {
            "objective": "parameter_value_producer",
            "consumer_method": "GET",
            "consumer_path": "/pets",
            "parameter_name": "cursor",
        }
    }
    retrieval_results = [
        app.capability_runtime.tool_executor.execute(
            tool_call=ToolCall(
                id=f"retrieve-{index}",
                name=retrieval_spec.name,
                arguments=retrieval_arguments,
            ),
            role="decision_maker",
            state={},
        )
        for index in range(2)
    ]

    assert first.status == second.status == "passed"
    assert [result.status for result in retrieval_results] == ["succeeded", "succeeded"]
    assert len(seen) == 1
    assert app.tool_context is context
    assert app.capability_runtime.tool_executor.tool_context is context
    assert context.baseline_schema_source["content"] != "changed"
    assert context.headers["Authorization"] == "Bearer runtime-secret"
    assert "runtime-secret" not in repr(context)
    assert all("runtime-secret" not in request.model_dump_json() for request in client.requests)

    with pytest.raises(ToolContextError) as exc_info:
        app.initialize(schema_source={"kind": "inline", "content": json.dumps(_spec())})
    assert exc_info.value.code == "tool_context_already_initialized"


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
    from restscope.openapi_parser import OpenAPIParser

    parsed = OpenAPIParser.parse(_spec())
    seen = []
    monkeypatch.setattr(OpenAPIParser, "parse", staticmethod(lambda source: seen.append(source) or parsed))
    app = _app(tmp_path)

    context = app.initialize(schema_source=schema_source)

    assert seen == [parser_input]
    assert dict(context.baseline_schema_source) == schema_source


def test_app_allows_retry_after_initialization_failure(monkeypatch, tmp_path) -> None:
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
    assert app.capability_runtime.tool_executor.tool_context is None

    context = app.initialize(schema_source={"kind": "inline", "content": "valid"})
    assert context.ir is parsed


def test_app_rejects_an_openapi_schema_without_operations_and_remains_retryable(tmp_path) -> None:
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
    from restscope.capabilities import ToolContextError

    app = _app(tmp_path)
    assert app.capability_runtime is not None

    with pytest.raises(ToolContextError) as exc_info:
        app.run(_request())
    assert exc_info.value.code == "tool_context_not_initialized"

    context = app.initialize(
        schema_source={"kind": "inline", "format": "json", "content": json.dumps(_spec())}
    )
    executor = app.capability_runtime.tool_executor
    assert executor.tool_context is context

    app.close()

    assert app.tool_context is None
    assert executor.tool_context is None
    with pytest.raises(RuntimeError, match="closed"):
        app.run(_request())
