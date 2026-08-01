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
    from restscope import RESTScopeApp
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    database = tmp_path / "app-context.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_URL=sqlite:///{database}\n", encoding="utf-8")
    return RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
    )


def _request():
    from restscope.supervisor import RESTScopeRunRequest

    return RESTScopeRunRequest()


def test_app_initializes_once_and_reuses_the_same_ir_across_runs(monkeypatch, tmp_path) -> None:
    """Scenario: verify that app initializes once and reuses the same ir across runs."""
    from restscope.capabilities import ToolContextError
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

    first = app.run(_request())
    second = app.run(_request())

    assert first.status == second.status == "passed"
    assert len(seen) == 1
    assert app.tool_context is context
    assert app.capability_runtime.require_context() is context
    assert context.baseline_schema_source["content"] != "changed"
    assert context.headers["Authorization"] == "Bearer runtime-secret"
    assert "runtime-secret" not in repr(context)

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
    from restscope.capabilities import ToolContextError
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
        app.capability_runtime.require_context()
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
    from restscope.capabilities import ToolContextError

    app = _app(tmp_path)
    assert app.capability_runtime is not None

    with pytest.raises(ToolContextError) as exc_info:
        app.run(_request())
    assert exc_info.value.code == "tool_context_not_initialized"

    context = app.initialize(
        schema_source={"kind": "inline", "format": "json", "content": json.dumps(_spec())}
    )
    runtime = app.capability_runtime
    assert runtime.require_context() is context

    app.close()

    assert app.tool_context is None
    with pytest.raises(ToolContextError) as exc_info:
        runtime.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"
    with pytest.raises(RuntimeError, match="closed"):
        app.run(_request())
