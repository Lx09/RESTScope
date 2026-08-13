"""Protect the shared semantic-handle and request-JSON Interface.

OpenAPI lookup, deterministic request generation, and the run-local Test Case
Catalog all use :class:`RequestInputReference`.  These tests use only that
public Interface so its internal path representation can change freely.
"""

from restscope.operation_references import RequestInputReference


def test_parameter_reference_reads_and_projects_one_direct_json_name() -> None:
    """A semantic handle locates the direct parameter name inside its location."""
    reference = RequestInputReference.parameter("query", "sort")
    request = {
        "path": {},
        "query": {"sort": "asc", "page": 2},
        "header": {},
        "cookie": {},
    }

    assert reference.handle == "query.sort"
    assert reference.read(request) == (True, "asc")
    assert reference.fragment(request) == {"query": {"sort": "asc"}}

    cookie = RequestInputReference.parameter("cookie", "mode")
    request["cookie"] = {"mode": "compact"}
    assert cookie.read(request) == (True, "compact")
    assert cookie.fragment(request) == {"cookie": {"mode": "compact"}}


def test_body_reference_preserves_the_smallest_complete_array_container() -> None:
    """Array projections keep real indices and never invent placeholder values."""
    reference = (
        RequestInputReference.body()
        .property("items")
        .items()
        .property("code")
    )
    request = {
        "path": {},
        "query": {},
        "header": {},
        "cookie": {},
        "body": {
            "items": [
                {"code": "A", "label": "first"},
                {"code": "B", "label": "second"},
            ],
            "unrelated": True,
        },
    }

    assert reference.handle == "body.items[].code"
    assert reference.read(request) == (True, ["A", "B"])
    assert reference.fragment(request) == {
        "body": {
            "items": [
                {"code": "A", "label": "first"},
                {"code": "B", "label": "second"},
            ]
        }
    }


def test_reference_reports_an_input_missing_without_confusing_json_null() -> None:
    """A sent null Body is present while an omitted Body is absent."""
    reference = RequestInputReference.body()
    without_body = {"path": {}, "query": {}, "header": {}, "cookie": {}}
    with_null_body = {**without_body, "body": None}

    assert reference.read(without_body) == (False, None)
    assert reference.read(with_null_body) == (True, None)
    assert reference.fragment(with_null_body) == {"body": None}


def test_openapi_and_testing_adapters_share_the_same_references() -> None:
    """Both operation representations must expose one canonical handle set."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation.semantics import build_semantic_input_map
    from restscope.request_generation.snapshot import build_initial_operation_config
    from restscope.tools.openapi import operation_input_references

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Inputs", "version": "1"},
            "paths": {
                "/items/{id}": {
                    "post": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "sort",
                                "in": "query",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "sort",
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
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "code": {"type": "string"},
                                                "metadata": {
                                                    "type": "object",
                                                    "properties": {
                                                        "label": {
                                                            "type": "string"
                                                        }
                                                    },
                                                },
                                                "choice": {
                                                    "oneOf": [
                                                        {"type": "string"},
                                                        {"type": "integer"},
                                                    ]
                                                },
                                            },
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /items/{id}"]
    config = build_initial_operation_config(operation)

    openapi_references = {
        item.handle for item in operation_input_references(operation)
    }
    testing_references = set(
        build_semantic_input_map(config).reference_by_handle
    )

    assert openapi_references == testing_references
    assert {
        "path.id",
        "query.sort",
        "header.sort",
        "cookie.mode",
        "body",
        "body[]",
        "body[].code",
        "body[].metadata.label",
        "body[].choice.oneOf[0]",
        "body[].choice.oneOf[1]",
    } <= openapi_references
