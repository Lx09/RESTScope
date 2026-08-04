"""Behavioral contracts for the four-tool global OpenAPI Capability."""

from __future__ import annotations


def _ir():
    """Build two operations with varied inputs, bodies, and response fallbacks."""
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
                                                "description": "Not model-visible",
                                                "example": "secret-example",
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
                        "responses": {
                            "201": {
                                "description": "created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "internal": {
                                                    "type": "string",
                                                    "writeOnly": True,
                                                },
                                                "items": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "name": {
                                                                "type": "string"
                                                            }
                                                        },
                                                    },
                                                },
                                            },
                                            "oneOf": [
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "kind": {
                                                            "type": "string"
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    }
                                },
                            },
                            "3XX": {
                                "description": "redirect",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "location": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            },
                            "4XX": {
                                "description": "client error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "message": {"type": "string"}
                                            },
                                        }
                                    },
                                    "application/problem+json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["errors"],
                                            "properties": {
                                                "errors": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "required": ["code"],
                                                        "properties": {
                                                            "code": {
                                                                "type": "string",
                                                                "enum": ["invalid", "missing"],
                                                            }
                                                        },
                                                    },
                                                }
                                            },
                                        }
                                    },
                                },
                            },
                            "default": {
                                "description": "other error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "detail": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            },
                        },
                    }
                },
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "healthy",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "status": {
                                                    "type": "string",
                                                    "enum": ["ok"],
                                                }
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
        }
    )


def _capability():
    """Bind one global Capability to a trusted in-memory ToolContext."""
    from restscope.capabilities import OpenAPICapability, ToolContext

    context = ToolContext(ir=_ir(), baseline_schema_source={})
    return OpenAPICapability(context_provider=lambda: context)


def _toolbox():
    """Register all four tools through the same production Interface."""
    from restscope.capabilities import (
        AgentToolbox,
        openapi_get_input_schema_tool_spec,
        openapi_get_response_field_schema_tool_spec,
        openapi_list_inputs_tool_spec,
        openapi_list_response_fields_tool_spec,
    )

    capability = _capability()
    toolbox = AgentToolbox()
    toolbox.register(
        spec=openapi_list_inputs_tool_spec(),
        execute=capability.list_inputs,
    )
    toolbox.register(
        spec=openapi_list_response_fields_tool_spec(),
        execute=capability.list_response_fields,
    )
    toolbox.register(
        spec=openapi_get_input_schema_tool_spec(),
        execute=capability.get_input_schema,
    )
    toolbox.register(
        spec=openapi_get_response_field_schema_tool_spec(),
        execute=capability.get_response_field_schema,
    )
    return toolbox


def _execute(name: str, arguments: dict):
    """Execute one model-shaped call through validation and output checking."""
    from restscope.llm import ToolCall

    return _toolbox().execute(
        ToolCall(id="openapi-query", name=name, arguments=arguments)
    )


def test_list_inputs_returns_only_one_bounded_page_of_handles() -> None:
    """Listing inputs stays compact and identifies duplicate Body media types."""
    result = _execute(
        "openapi.list_inputs",
        {"operation_key": "POST /projects/{id}", "offset": 0, "limit": 2},
    )

    assert result.status == "succeeded"
    assert result.structured["total"] > 2
    assert result.structured["next_offset"] == 2
    assert len(result.structured["inputs"]) == 2
    assert "schema" not in repr(result.structured)


def test_list_inputs_filters_body_media_but_keeps_ordinary_parameters() -> None:
    """A media filter narrows Body duplicates without hiding path/query inputs."""
    result = _execute(
        "openapi.list_inputs",
        {
            "operation_key": "POST /projects/{id}",
            "media_type": "application/json",
            "prefix": "body.name",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["inputs"] == [
        {"name": "body.name", "media_type": "application/json"}
    ]

    all_names = {
        item["name"]
        for item in _execute(
            "openapi.list_inputs",
            {
                "operation_key": "POST /projects/{id}",
                "media_type": "application/json",
            },
        ).structured["inputs"]
    }
    assert {"path.id", "query.page", "header.x-trace", "cookie.mode"} <= all_names


def test_list_response_fields_returns_one_compact_page() -> None:
    """Response discovery returns sorted handles without exposing Schemas."""
    result = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "limit": 2,
        },
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "operation_key": "POST /projects/{id}",
        "requested_status_code": "201",
        "matched_status_code": "201",
        "media_type": "application/json",
        "fields": [{"name": "body"}, {"name": "body.id"}],
        "total": 7,
        "offset": 0,
        "next_offset": 2,
    }
    assert "schema" not in repr(result.structured)


def test_list_response_fields_reuses_schema_traversal_rules() -> None:
    """Arrays and combiners are listed while write-only response fields stay hidden."""
    result = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": "201"},
    )

    assert result.status == "succeeded"
    assert [field["name"] for field in result.structured["fields"]] == [
        "body",
        "body.id",
        "body.items",
        "body.items[]",
        "body.items[].name",
        "body.oneOf[0]",
        "body.oneOf[0].kind",
    ]
    assert "body.internal" not in repr(result.structured)


def test_list_response_fields_matches_status_fallbacks() -> None:
    """The list query shares exact, class-wildcard, and default response matching."""
    exact = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 201},
    )
    wildcard = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 302},
    )
    fallback = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 503},
    )

    assert exact.structured["matched_status_code"] == "201"
    assert wildcard.structured["matched_status_code"] == "3XX"
    assert fallback.structured["matched_status_code"] == "default"


def test_list_response_fields_has_only_the_approved_inputs() -> None:
    """Media selection and prefix filtering stay outside this narrow Interface."""
    from restscope.capabilities import openapi_list_response_fields_tool_spec

    spec = openapi_list_response_fields_tool_spec()
    extra_argument = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "media_type": "application/json",
        },
    )
    missing_status = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}"},
    )
    excessive_limit = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "limit": 201,
        },
    )
    beyond_end = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "GET /health",
            "status_code": 200,
            "offset": 100,
        },
    )

    assert set(spec.input_schema["properties"]) == {
        "operation_key",
        "status_code",
        "offset",
        "limit",
    }
    assert spec.input_schema["required"] == ["operation_key", "status_code"]
    assert extra_argument.status == "denied"
    assert extra_argument.error["code"] == "invalid_tool_arguments"
    assert missing_status.status == "denied"
    assert missing_status.error["code"] == "invalid_tool_arguments"
    assert excessive_limit.status == "denied"
    assert excessive_limit.error["code"] == "invalid_tool_arguments"
    assert beyond_end.status == "succeeded"
    assert beyond_end.structured["fields"] == []
    assert beyond_end.structured["total"] == 2
    assert "next_offset" not in beyond_end.structured


def test_list_response_fields_reports_media_ambiguity() -> None:
    """A document that violates the single-media assumption still fails safely."""
    result = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 400},
    )

    assert result.status == "failed"
    assert result.error["code"] == "openapi_media_type_ambiguous"


def test_one_global_capability_can_select_another_exact_operation() -> None:
    """Operation scope comes from each call instead of Capability construction."""
    result = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "GET /health",
            "status_code": 200,
            "field": "body.status",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["operation_key"] == "GET /health"
    assert result.structured["schema"]["enum"] == ["ok"]


def test_get_input_schema_returns_only_the_exact_node_summary() -> None:
    """A unique JSON body is selected without returning prose or sibling fields."""
    result = _execute(
        "openapi.get_input_schema",
        {
            "operation_key": "POST /projects/{id}",
            "input": "body.name",
        },
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "operation_key": "POST /projects/{id}",
        "input": "body.name",
        "location": "body",
        "required": True,
        "media_type": "application/json",
        "schema": {"type": "string", "min_length": 3},
    }
    assert "description" not in repr(result.structured)
    assert "example" not in repr(result.structured)


def test_non_body_input_rejects_an_irrelevant_media_type() -> None:
    """The narrow Interface reports caller mistakes instead of ignoring them."""
    result = _execute(
        "openapi.get_input_schema",
        {
            "operation_key": "POST /projects/{id}",
            "input": "path.id",
            "media_type": "application/json",
        },
    )

    assert result.status == "failed"
    assert result.error["code"] == "openapi_input_media_type_not_allowed"


def test_response_lookup_matches_wildcard_and_normalizes_array_indexes() -> None:
    """Concrete failed-response paths resolve to their OpenAPI array item node."""
    result = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 422,
            "field": "body.errors[0].code",
            "media_type": "application/problem+json",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["requested_status_code"] == "422"
    assert result.structured["matched_status_code"] == "4XX"
    assert result.structured["field"] == "body.errors[].code"
    assert result.structured["required"] is True
    assert result.structured["schema"]["enum"] == ["invalid", "missing"]


def test_response_lookup_uses_default_and_reports_media_ambiguity() -> None:
    """Fallback is deterministic while multiple JSON contracts require a choice."""
    fallback = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 503,
            "field": "body.detail",
        },
    )
    ambiguous = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 400,
            "field": "body.message",
        },
    )

    assert fallback.status == "succeeded"
    assert fallback.structured["matched_status_code"] == "default"
    assert ambiguous.status == "failed"
    assert ambiguous.error["code"] == "openapi_media_type_ambiguous"


def test_unknown_operation_and_old_tool_name_are_not_accepted() -> None:
    """Global lookup remains exact and the deleted scoped tool has no alias."""
    missing = _execute(
        "openapi.list_inputs",
        {"operation_key": "GET /missing"},
    )
    missing_response = _execute(
        "openapi.list_response_fields",
        {"operation_key": "GET /missing", "status_code": 200},
    )
    old = _execute("openapi.lookup_operation", {})

    assert missing.status == "failed"
    assert missing.error["code"] == "openapi_operation_not_found"
    assert missing_response.status == "failed"
    assert missing_response.error["code"] == "openapi_operation_not_found"
    assert old.status == "denied"
    assert old.error["code"] == "unknown_tool"
