"""Protect durable Batch and Test Case query Tool contracts."""

from __future__ import annotations

from datetime import UTC, datetime


def _catalog():
    """Create a fresh real Catalog so query tests cover SQL ordering too."""

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return APIBehaviorCatalog(lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions))


def test_get_batch_results_groups_one_stable_page() -> None:
    """A page groups IDs by operation, outcome, and nullable status code."""

    from restscope.api_behavior_monitor.catalog import (
        BatchWrite,
        ObservationWrite,
        OperationDefinition,
    )
    from restscope.tools.test_case import TestCaseQueryToolBackend

    catalog = _catalog()
    for operation_key in ("GET /items", "POST /items"):
        method, path = operation_key.split(" ", 1)
        catalog.ensure_operation(
            OperationDefinition(
                operation_id=operation_key,
                method=method,
                path=path,
            )
        )
    batch = catalog.create_batch(BatchWrite(summary={"status": "completed"}))
    writes = (
        ObservationWrite(
            operation_id="GET /items",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            outcome_kind="http",
            request_json={"path": {}},
            status_code=200,
            reason_phrase="OK",
            media_type="application/json",
            response_headers={"content-type": "application/json"},
            response_body=b"{}",
            body_format="json",
            batch_id=batch.batch_id,
            batch_case_index=0,
        ),
        ObservationWrite(
            operation_id="GET /items",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            outcome_kind="http",
            request_json={"path": {}},
            status_code=404,
            reason_phrase="Not Found",
            media_type="text/plain",
            response_headers={"content-type": "text/plain"},
            response_body=b"missing",
            body_format="text",
            batch_id=batch.batch_id,
            batch_case_index=1,
        ),
        ObservationWrite(
            operation_id="POST /items",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            outcome_kind="transport",
            request_json={"body": {"name": "one"}},
            transport_code="request_timeout",
            transport_message="HTTP request timed out",
            batch_id=batch.batch_id,
            batch_case_index=2,
        ),
    )
    records = [catalog.record_observation(item) for item in writes]
    backend = TestCaseQueryToolBackend(catalog=catalog)

    page = backend.get_batch_results(
        batch_id=batch.batch_id,
        offset=1,
        limit=2,
    )["structured"]

    assert page == {
        "status": "found",
        "batch_id": batch.batch_id,
        "summary": {"status": "completed"},
        "total": 3,
        "offset": 1,
        "groups": [
            {
                "operation_key": "GET /items",
                "outcome_kind": "http",
                "status_code": 404,
                "observation_ids": [records[1].observation_id],
            },
            {
                "operation_key": "POST /items",
                "outcome_kind": "transport",
                "status_code": None,
                "observation_ids": [records[2].observation_id],
            },
        ],
    }


def test_query_tools_return_structured_not_found() -> None:
    """Unknown durable identities are successful, correctable query results."""

    from restscope.tools.test_case import TestCaseQueryToolBackend

    backend = TestCaseQueryToolBackend(catalog=_catalog())

    assert backend.get_batch_results(batch_id="missing")["structured"] == {
        "status": "not_found",
        "batch_id": "missing",
        "summary": None,
        "total": 0,
        "offset": 0,
        "groups": [],
    }
    assert backend.get(test_case_id="missing")["structured"] == {
        "status": "not_found",
        "test_case_id": "missing",
        "observation": None,
    }


def test_get_test_case_bounds_binary_body_and_redacts_sensitive_headers() -> None:
    """The database keeps exact bytes while Agent-visible output is safe and bounded."""

    import base64

    from restscope.api_behavior_monitor.catalog import ObservationWrite, OperationDefinition
    from restscope.tools.test_case import TestCaseQueryToolBackend

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /download",
            method="GET",
            path="/download",
        )
    )
    body = bytes(range(256)) * 80
    record = catalog.record_observation(
        ObservationWrite(
            operation_id="GET /download",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            outcome_kind="http",
            request_json={"path": {}},
            status_code=200,
            reason_phrase="OK",
            media_type="application/octet-stream",
            response_headers={
                "content-type": "application/octet-stream",
                "set-cookie": "session=secret",
                "x-api-token": "secret-token",
            },
            response_body=body,
            body_format="base64",
        )
    )

    result = TestCaseQueryToolBackend(catalog=catalog).get(
        test_case_id=record.observation_id
    )["structured"]
    observation = result["observation"]

    assert result["status"] == "found"
    assert observation["response_headers"] == {
        "content-type": "application/octet-stream",
        "set-cookie": "[REDACTED]",
        "x-api-token": "[REDACTED]",
    }
    projection = observation["response_body"]
    assert projection["format"] == "base64"
    assert projection["size_bytes"] == len(body)
    assert projection["truncated"] is True
    assert base64.b64decode(projection["value"]) == body[: 16 * 1024]
    assert catalog.get_observation(record.observation_id).response_body == body


def test_get_test_case_returns_json_source_and_utf8_text() -> None:
    """Readable HTTP bodies keep their persisted JSON or text representation."""

    from restscope.api_behavior_monitor.catalog import ObservationWrite, OperationDefinition
    from restscope.tools.test_case import TestCaseQueryToolBackend

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(operation_id="GET /text", method="GET", path="/text")
    )
    records = [
        catalog.record_observation(
            ObservationWrite(
                operation_id="GET /text",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                outcome_kind="http",
                request_json={"path": {}},
                status_code=200,
                media_type=media_type,
                response_headers={"content-type": media_type},
                response_body=body,
                body_format=body_format,
            )
        )
        for media_type, body, body_format in (
            ("application/json", b'{ "answer" : 42 }', "json"),
            ("text/plain", "你好 RESTScope".encode(), "text"),
        )
    ]
    backend = TestCaseQueryToolBackend(catalog=catalog)

    json_body = backend.get(test_case_id=records[0].observation_id)["structured"][
        "observation"
    ]["response_body"]
    text_body = backend.get(test_case_id=records[1].observation_id)["structured"][
        "observation"
    ]["response_body"]

    assert json_body == {
        "format": "json",
        "value": '{ "answer" : 42 }',
        "size_bytes": 17,
        "truncated": False,
    }
    assert text_body["format"] == "text"
    assert text_body["value"] == "你好 RESTScope"
    assert text_body["truncated"] is False


def test_new_test_case_tool_schemas_are_closed_and_bounded() -> None:
    """Tool Schemas express required identities and pagination limits."""

    from restscope.tools.test_case import (
        test_case_get_batch_results_tool_spec,
        test_case_get_tool_spec,
    )

    batch = test_case_get_batch_results_tool_spec()
    test_case = test_case_get_tool_spec()

    assert batch.input_schema["required"] == ["batch_id"]
    assert batch.input_schema["properties"]["limit"]["maximum"] == 200
    assert batch.input_schema["additionalProperties"] is False
    assert batch.output_schema["additionalProperties"] is False
    assert test_case.input_schema["required"] == ["test_case_id"]
    assert test_case.input_schema["additionalProperties"] is False
    assert test_case.output_schema["additionalProperties"] is False


def test_query_tools_validate_inputs_and_successful_outputs_in_the_toolbox() -> None:
    """The Harness rejects oversized pages and validates structured not-found output."""

    from restscope.llm import ToolCall
    from restscope.tools import AgentToolbox, builtin_tool_catalog
    from restscope.tools.test_case import (
        TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME,
        TEST_CASE_GET_TOOL_NAME,
        TestCaseQueryToolBackend,
        test_case_query_tool_bindings,
    )

    backend = TestCaseQueryToolBackend(catalog=_catalog())
    selected = (
        TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME,
        TEST_CASE_GET_TOOL_NAME,
    )
    toolbox = AgentToolbox.from_catalog(
        catalog=builtin_tool_catalog(),
        selected_names=selected,
        bindings=list(test_case_query_tool_bindings(backend)),
    )

    invalid = toolbox.execute(
        ToolCall(
            id="invalid-page",
            name=TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME,
            arguments={"batch_id": "missing", "limit": 201},
        )
    )
    missing = toolbox.execute(
        ToolCall(
            id="missing-case",
            name=TEST_CASE_GET_TOOL_NAME,
            arguments={"test_case_id": "missing"},
        )
    )

    assert invalid.status == "denied"
    assert invalid.error["code"] == "invalid_tool_arguments"
    assert missing.status == "succeeded"
    assert missing.structured == {
        "status": "not_found",
        "test_case_id": "missing",
        "observation": None,
    }
