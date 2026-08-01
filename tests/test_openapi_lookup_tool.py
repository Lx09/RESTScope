"""Contracts for the Agent-scoped OpenAPI operation lookup capability."""

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


def test_scoped_openapi_lookup_lists_semantic_parameters_as_json() -> None:
    """Dedup can discover all request handles without receiving them in its prompt."""
    from restscope.capabilities import AgentToolbox
    from restscope.capabilities.openapi_lookup import (
        lookup_operation,
        scoped_openapi_lookup_tool_spec,
    )
    from restscope.llm import ToolCall

    operation = _ir().operations["POST /projects/{id}"]
    spec = scoped_openapi_lookup_tool_spec(operation)
    toolbox = AgentToolbox()
    toolbox.register(
        spec=spec,
        execute=lambda: lookup_operation(operation),
    )
    result = toolbox.execute(
        ToolCall(
            id="lookup-1",
            name=spec.name,
            arguments={},
        )
    )

    assert spec.input_schema["properties"] == {}
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


def test_scoped_openapi_lookup_rejects_a_caller_selected_operation() -> None:
    """A model cannot forge an operation key at this Agent boundary."""
    from restscope.capabilities import AgentToolbox
    from restscope.capabilities.openapi_lookup import (
        lookup_operation,
        scoped_openapi_lookup_tool_spec,
    )
    from restscope.llm import ToolCall

    operation = _ir().operations["POST /projects/{id}"]
    toolbox = AgentToolbox()
    toolbox.register(
        spec=scoped_openapi_lookup_tool_spec(operation),
        execute=lambda: lookup_operation(operation),
    )

    result = toolbox.execute(
        ToolCall(
            id="lookup-missing",
            name="openapi.lookup_operation",
            arguments={"operation_key": "GET /missing"},
        )
    )

    assert result.status == "denied"
    assert result.error["code"] == "invalid_tool_arguments"
