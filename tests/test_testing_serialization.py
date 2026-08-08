"""Regression scenarios for testing serialization. Each test documents one observable contract or failure boundary."""

from __future__ import annotations


def _snapshot(operation):
    from restscope.harness.testing.snapshot import build_operation_snapshot

    snapshot, _ = build_operation_snapshot(operation)
    return snapshot


def test_openapi_serializer_applies_parameter_styles_and_json_body() -> None:
    """Scenario: verify that openapi serializer applies parameter styles and json body."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratedTestCase
    from restscope.harness.testing.serialization import serialize_test_case

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Serialization", "version": "1"},
        "paths": {
            "/users/{userId}": {
                "post": {
                    "parameters": [
                        {"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}},
                        {
                            "name": "tags",
                            "in": "query",
                            "style": "form",
                            "explode": False,
                            "schema": {"type": "array", "items": {"type": "string"}},
                        },
                        {
                            "name": "filter",
                            "in": "query",
                            "style": "deepObject",
                            "explode": True,
                            "schema": {"type": "object"},
                        },
                        {
                            "name": "X-Flags",
                            "in": "header",
                            "style": "simple",
                            "explode": False,
                            "schema": {"type": "array", "items": {"type": "string"}},
                        },
                        {"name": "session", "in": "cookie", "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    operation = OpenAPIParser.parse(spec).operations["POST /users/{userId}"]
    case = GeneratedTestCase(
        operation_key=operation.operation_key,
        case_index=0,
        media_type="application/json",
        path_parameters={"userId": "a/b"},
        query_parameters={"tags": ["new", "sale"], "filter": {"role": "admin", "active": True}},
        header_parameters={"X-Flags": ["one", "two"]},
        cookie_parameters={"session": "cookie-value"},
        body={"name": "Ada"},
        generated_values=[],
        omitted_input_node_ids=[],
    )

    request = serialize_test_case(_snapshot(operation), case)

    assert request.method == "POST"
    assert request.path == "/users/a%2Fb"
    assert request.query_items == [
        ("tags", "new,sale"),
        ("filter[active]", "true"),
        ("filter[role]", "admin"),
    ]
    assert request.headers == {
        "X-Flags": "one,two",
        "Cookie": "session=cookie-value",
        "Content-Type": "application/json",
    }
    assert request.content == b'{"name":"Ada"}'


def test_openapi_serializer_supports_text_form_and_swagger_collection_formats() -> None:
    """Scenario: verify that openapi serializer supports text form and swagger collection formats."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratedTestCase
    from restscope.harness.testing.serialization import serialize_test_case

    swagger_operation = OpenAPIParser.parse(
        {
            "swagger": "2.0",
            "info": {"title": "Legacy", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "tags",
                                "in": "query",
                                "type": "array",
                                "items": {"type": "string"},
                                "collectionFormat": "pipes",
                            },
                            {
                                "name": "defaultTags",
                                "in": "query",
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["GET /search"]
    swagger_request = serialize_test_case(
        _snapshot(swagger_operation),
        GeneratedTestCase(
            operation_key=swagger_operation.operation_key,
            case_index=0,
            path_parameters={},
            query_parameters={
                "tags": ["one", "two"],
                "defaultTags": ["three", "four"],
            },
            header_parameters={},
            cookie_parameters={},
            generated_values=[],
            omitted_input_node_ids=[],
        ),
    )
    assert swagger_request.query_items == [
        ("tags", "one|two"),
        ("defaultTags", "three,four"),
    ]

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Bodies", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "text/plain": {"schema": {"type": "string"}},
                                "application/x-www-form-urlencoded": {
                                    "schema": {"type": "object"}
                                },
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /submit"]

    def body_case(media_type, body):
        return GeneratedTestCase(
            operation_key=operation.operation_key,
            case_index=0,
            media_type=media_type,
            path_parameters={},
            query_parameters={},
            header_parameters={},
            cookie_parameters={},
            body=body,
            generated_values=[],
            omitted_input_node_ids=[],
        )

    assert serialize_test_case(_snapshot(operation), body_case("text/plain", "hello")).content == b"hello"
    form = serialize_test_case(
        _snapshot(operation),
        body_case("application/x-www-form-urlencoded", {"tag": ["a", "b"], "ok": True}),
    )
    assert form.content == b"tag=a&tag=b&ok=true"


def test_openapi_serializer_builds_deterministic_non_file_multipart_body() -> None:
    """Multipart object fields become stable text or JSON form-data parts."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratedTestCase
    from restscope.harness.testing.serialization import serialize_test_case

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Multipart", "version": "1"},
            "paths": {
                "/projects": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "enabled": {"type": "boolean"},
                                            "tags": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "settings": {"type": "object"},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
    ).operations["POST /projects"]
    case = GeneratedTestCase(
        operation_key=operation.operation_key,
        case_index=0,
        media_type="multipart/form-data",
        path_parameters={},
        query_parameters={},
        header_parameters={},
        cookie_parameters={},
        body={
            "name": "demo",
            "count": 3,
            "enabled": True,
            "tags": ["api", "smoke"],
            "settings": {"archived": False},
        },
        body_present=True,
        generated_values=[],
        omitted_input_node_ids=[],
    )

    first = serialize_test_case(_snapshot(operation), case)
    second = serialize_test_case(_snapshot(operation), case)

    assert first == second
    content_type = first.headers["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.removeprefix("multipart/form-data; boundary=")
    assert boundary.startswith("restscope-")
    body = first.content.decode()
    assert body.startswith(f"--{boundary}\r\n")
    assert body.endswith(f"--{boundary}--\r\n")
    assert body.count(f"--{boundary}\r\n") == 5
    assert (
        'Content-Disposition: form-data; name="count"\r\n'
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "3\r\n"
    ) in body
    assert (
        'Content-Disposition: form-data; name="enabled"\r\n'
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        "true\r\n"
    ) in body
    assert (
        'Content-Disposition: form-data; name="tags"\r\n'
        "Content-Type: application/json\r\n\r\n"
        '["api","smoke"]\r\n'
    ) in body
    assert (
        'Content-Disposition: form-data; name="settings"\r\n'
        "Content-Type: application/json\r\n\r\n"
        '{"archived":false}\r\n'
    ) in body


def test_multipart_serializer_rejects_file_values_and_header_injection() -> None:
    """File payloads and CR/LF field names cannot enter multipart wire headers."""
    import pytest

    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratedTestCase
    from restscope.harness.testing.serialization import (
        SerializationError,
        serialize_test_case,
    )

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Multipart safety", "version": "1"},
            "paths": {
                "/submit": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "multipart/form-data": {
                                    "schema": {"type": "object"}
                                }
                            }
                        },
                        "responses": {"204": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["POST /submit"]

    def body_case(body):
        return GeneratedTestCase(
            operation_key=operation.operation_key,
            case_index=0,
            media_type="multipart/form-data",
            path_parameters={},
            query_parameters={},
            header_parameters={},
            cookie_parameters={},
            body=body,
            body_present=True,
            generated_values=[],
            omitted_input_node_ids=[],
        )

    with pytest.raises(SerializationError, match="file values"):
        serialize_test_case(_snapshot(operation), body_case({"avatar": b"png"}))
    with pytest.raises(SerializationError, match="CR or LF"):
        serialize_test_case(
            _snapshot(operation),
            body_case({"safe\r\nX-Injected: yes": "value"}),
        )


def test_allow_reserved_query_values_are_preserved_by_target_url_preparation() -> None:
    """Scenario: verify that allow reserved query values are preserved by target url preparation."""
    from restscope.http_transport import build_target_url
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratedTestCase
    from restscope.harness.testing.execution import _target_query_items
    from restscope.harness.testing.serialization import serialize_test_case

    operation = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Reserved query", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "q",
                                "in": "query",
                                "allowReserved": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    ).operations["GET /search"]
    request = serialize_test_case(
        _snapshot(operation),
        GeneratedTestCase(
            operation_key=operation.operation_key,
            case_index=0,
            path_parameters={},
            query_parameters={"q": "a/b?c&admin=true#section"},
            header_parameters={},
            cookie_parameters={},
            generated_values=[],
            omitted_input_node_ids=[],
        ),
    )

    assert request.query_allow_reserved_indices == [0]
    url = build_target_url(
        "https://api.example.test",
        request.path,
        _target_query_items(request),
    )
    assert str(url) == (
        "https://api.example.test/search?q=a/b?c&admin=true%23section"
    )
