"""Contracts for the globally registered OpenAPI operation lookup capability."""

from __future__ import annotations


def _ir():
    """Build one operation with every request Parameter location and two bodies."""
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Lookup", "version": "1"},
            "paths": {
                "/projects/{id}": {
                    "post": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer", "minimum": 1},
                            },
                            {
                                "name": "page",
                                "in": "query",
                                "schema": {"type": "integer", "maximum": 100},
                            },
                            {
                                "name": "X-Trace",
                                "in": "header",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "mode",
                                "in": "cookie",
                                "schema": {"type": "string"},
                            },
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "minLength": 3,
                                            }
                                        },
                                    }
                                },
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "avatar": {
                                                "type": "string",
                                                "format": "binary",
                                            },
                                        },
                                    }
                                },
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
    )


def test_global_openapi_lookup_lists_semantic_parameters_as_json() -> None:
    """Dedup can discover all request handles without receiving them in its prompt."""
    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.llm import ToolCall

    runtime = build_capabilities()
    runtime.tool_executor.bind_context(
        ToolContext(ir=_ir(), baseline_schema_source={})
    )

    spec = runtime.tool_registry.get_spec("openapi.lookup_operation")
    result = runtime.tool_executor.execute(
        tool_call=ToolCall(
            id="lookup-1",
            name=spec.name,
            arguments={"operation_key": "POST /projects/{id}"},
        ),
        role="operation_smoke_failure_dedup",
        state={},
    )

    assert spec.read_only is True
    assert result.status == "succeeded"
    assert result.content is None
    assert result.structured["operation"] == {
        "operation_key": "POST /projects/{id}",
        "method": "POST",
        "path": "/projects/{id}",
    }
    assert {
        item["name"] for item in result.structured["parameters"]
    } >= {
        "path.id",
        "query.page",
        # Header handles follow the existing Solve/Patch convention and are
        # case-normalized even though the source document used ``X-Trace``.
        "header.x-trace",
        "cookie.mode",
    }
    bodies = {
        item["media_type"]: {
            parameter["name"] for parameter in item["parameters"]
        }
        for item in result.structured["request_bodies"]
    }
    assert bodies["application/json"] >= {"body", "body.name"}
    assert bodies["multipart/form-data"] >= {
        "body",
        "body.name",
        "body.avatar",
    }
    assert "description" not in repr(result.structured)
    assert "example" not in repr(result.structured)


def test_openapi_lookup_returns_a_structured_unknown_operation_failure() -> None:
    """A forged operation key cannot expose another document fragment."""
    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.llm import ToolCall

    runtime = build_capabilities()
    runtime.tool_executor.bind_context(
        ToolContext(ir=_ir(), baseline_schema_source={})
    )

    result = runtime.tool_executor.execute(
        tool_call=ToolCall(
            id="lookup-missing",
            name="openapi.lookup_operation",
            arguments={"operation_key": "GET /missing"},
        ),
        role="operation_smoke_failure_dedup",
        state={},
    )

    assert result.status == "failed"
    assert result.error["code"] == "openapi_operation_not_found"
