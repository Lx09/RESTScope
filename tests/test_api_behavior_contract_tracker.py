"""Regression scenarios for api behavior contract tracker. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from restscope.openapi_parser import OpenAPIParser


def _ir_with_wildcard_response():
    return OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "behavior monitor", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {
                            "2XX": {
                                "description": "successful response",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["id"],
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "legacy": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )


def test_first_exact_status_materializes_from_wildcard_and_widens_ir() -> None:
    """Scenario: verify that first exact status materializes from wildcard and widens ir."""
    from restscope.api_behavior_monitor.response_contracts import (
        ResponseContractTracker,
    )

    ir = _ir_with_wildcard_response()
    operation = ir.operations["GET /items"]
    wildcard = operation.responses.by_status["2XX"]
    tracker = ResponseContractTracker()

    result = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=201,
        media_type="Application/JSON; charset=utf-8",
        body=b'{"id": "generated", "name": "first"}',
    )

    assert result.status == "updated"
    assert result.key.status_code == 201
    assert result.key.media_type == "application/json"
    exact = operation.responses.by_status["201"]
    assert exact is not wildcard
    assert "name" not in wildcard.contents["application/json"].schema.properties
    schema = exact.contents["application/json"].schema
    assert schema is not None
    assert schema.required == ["id"]
    assert schema.properties["id"].type == ["integer", "string"]
    assert schema.properties["name"].type == "string"
    assert "name" not in schema.required
    assert "legacy" in schema.properties


def test_checked_response_key_is_not_merged_twice() -> None:
    """Scenario: verify that checked response key is not merged twice."""
    from restscope.api_behavior_monitor.response_contracts import (
        ResponseContractTracker,
    )

    ir = _ir_with_wildcard_response()
    tracker = ResponseContractTracker()
    first = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=200,
        media_type="application/json",
        body=b'{"id": 1}',
    )
    second = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=200,
        media_type="application/json",
        body=b'{"id": 2, "late": true}',
    )

    assert first.status in {"matched", "updated"}
    assert second.status == "already_checked"
    exact = ir.operations["GET /items"].responses.by_status["200"]
    schema = exact.contents["application/json"].schema
    assert schema is not None
    assert "late" not in schema.properties


def test_invalid_or_truncated_json_remains_pending_and_retries() -> None:
    """Scenario: verify that invalid or truncated json remains pending and retries."""
    from restscope.api_behavior_monitor.response_contracts import (
        ResponseContractTracker,
    )

    ir = _ir_with_wildcard_response()
    tracker = ResponseContractTracker()

    invalid = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=202,
        media_type="application/json",
        body=b"{",
    )
    truncated = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=202,
        media_type="application/json",
        body=b'{"id":',
        body_truncated=True,
    )
    valid = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=202,
        media_type="application/json",
        body=b'{"id": 7, "accepted": true}',
    )

    assert invalid.status == "pending_retry"
    assert truncated.status == "pending_retry"
    assert valid.status == "updated"
    exact = ir.operations["GET /items"].responses.by_status["202"]
    schema = exact.contents["application/json"].schema
    assert schema is not None
    assert schema.properties["accepted"].type == "boolean"


def test_text_empty_and_binary_responses_create_conservative_exact_contracts() -> None:
    """Scenario: verify that text empty and binary responses create conservative exact contracts."""
    from restscope.api_behavior_monitor.response_contracts import (
        ResponseContractTracker,
    )

    ir = _ir_with_wildcard_response()
    tracker = ResponseContractTracker()

    text = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=400,
        media_type="text/plain; charset=utf-8",
        body=b"bad request",
    )
    empty = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=204,
        media_type="application/json",
        body=b"",
    )
    binary = tracker.observe(
        ir=ir,
        operation_key="GET /items",
        status_code=503,
        media_type="application/octet-stream",
        body=b"\x00\x01",
    )

    assert text.status == "updated"
    text_schema = ir.operations["GET /items"].responses.by_status["400"].contents[
        "text/plain"
    ].schema
    assert text_schema is not None
    assert text_schema.type == "string"

    assert empty.status == "updated"
    assert not ir.operations["GET /items"].responses.by_status["204"].contents

    assert binary.status == "updated"
    binary_media = ir.operations["GET /items"].responses.by_status["503"].contents[
        "application/octet-stream"
    ]
    assert binary_media.schema is None
