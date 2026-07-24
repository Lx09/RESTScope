from __future__ import annotations

from sqlalchemy.pool import StaticPool

from restscope.openapi_parser import OpenAPIParser


def _catalog():
    from restscope.agent.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalog,
    )
    from restscope.db import (
        Base,
        SqlAlchemyResponseValueCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return ResponseValueCatalog(
        lambda: SqlAlchemyResponseValueCatalogUnitOfWork(
            make_session_factory(engine)
        )
    )


def _response_value_ir():
    return OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "response values", "version": "1"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "responses": {
                            "200": {
                                "description": "users",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "data": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "user_id": {
                                                                "type": "integer"
                                                            },
                                                            "display_name": {
                                                                "type": "string"
                                                            },
                                                        },
                                                    },
                                                }
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/profiles/{userId}": {
                    "get": {
                        "operationId": "getProfile",
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "profile"}},
                    }
                },
            },
        }
    )


def test_registers_ir_source_extracts_values_and_deduplicates_them() -> None:
    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    ir = _response_value_ir()

    observed = tracker.observe(
        producer_operation_key="GET /users",
        status_code=200,
        media_type="application/json; charset=utf-8",
        body={
            "data": [
                {"user_id": 7, "display_name": "one"},
                {"user_id": 7, "display_name": "duplicate"},
                {"user_id": 9, "display_name": "two"},
            ]
        },
    )
    preview = tracker.preview(
        ir=ir,
        consumer_operation_key="GET /profiles/{userId}",
        consumer_input_node_id="GET /profiles/{userId}::parameter::path::userId",
        parameter_name="userId",
        expected_type="integer",
    )
    assert preview is not None
    assert preview.value_count == 2
    assert [source.selector for source in preview.sources] == [
        "$.data[].user_id"
    ]

    registration = tracker.register(
        ir=ir,
        consumer_operation_key="GET /profiles/{userId}",
        consumer_input_node_id="GET /profiles/{userId}::parameter::path::userId",
        parameter_name="userId",
        expected_type="integer",
    )

    assert registration.status == "registered"
    assert registration.value_name.startswith("response_")
    assert [
        (
            source.producer_operation_key,
            source.status_code,
            source.media_type,
            source.selector,
        )
        for source in registration.sources
    ] == [
        (
            "GET /users",
            "200",
            "application/json",
            "$.data[].user_id",
        )
    ]
    assert observed.values_recorded == 0
    assert catalog.values_for(registration.value_name) == [7, 9]

    first = tracker.observe(
        producer_operation_key="GET /users",
        status_code=200,
        media_type="application/json; charset=utf-8",
        body={
            "data": [
                {"user_id": 7, "display_name": "one"},
                {"user_id": 7, "display_name": "duplicate"},
                {"user_id": 9, "display_name": "two"},
            ]
        },
    )
    second = tracker.observe(
        producer_operation_key="GET /users",
        status_code=200,
        media_type="application/json",
        body={"data": [{"user_id": 9}]},
    )

    assert first.values_recorded == 0
    assert second.values_recorded == 0
    assert catalog.values_for(registration.value_name) == [7, 9]


def test_observation_history_flattens_all_scalars_and_keeps_latest_100() -> None:
    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )
    from restscope.agent.api_behavior_monitor.response_value_catalog import (
        ResponseValueSource,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    for sequence in range(101):
        tracker.observe(
            producer_operation_key="GET /producer",
            status_code=200,
            media_type="application/json",
            body={
                "sequence": sequence,
                "token": f"secret-{sequence}",
                "nested": {
                    "enabled": sequence % 2 == 0,
                    "ratio": sequence + 0.5,
                    "ignored": None,
                },
                "items": [{"value": f"value-{sequence}"}],
            },
        )

    def values(selector: str) -> list[object]:
        return catalog.historical_values_for_source(
            ResponseValueSource(
                producer_operation_key="GET /producer",
                status_code="200",
                media_type="application/json",
                selector=selector,
                field_name=selector.rsplit(".", 1)[-1],
            )
        )

    assert values("$.sequence") == list(range(1, 101))
    assert values("$.token") == [f"secret-{index}" for index in range(1, 101)]
    assert values("$.nested.enabled") == [False, True]
    assert values("$.nested.ratio") == [
        index + 0.5 for index in range(1, 101)
    ]
    assert values("$.items[].value") == [
        f"value-{index}" for index in range(1, 101)
    ]
    assert values("$.nested.ignored") == []


def test_observation_history_ignores_non_2xx_and_non_json() -> None:
    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )
    from restscope.agent.api_behavior_monitor.response_value_catalog import (
        ResponseValueSource,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    tracker.observe(
        producer_operation_key="GET /producer",
        status_code=400,
        media_type="application/json",
        body={"value": "bad-request"},
    )
    tracker.observe(
        producer_operation_key="GET /producer",
        status_code=200,
        media_type="text/plain",
        body={"value": "not-json"},
    )

    for status_code, media_type in (
        ("400", "application/json"),
        ("200", "text/plain"),
    ):
        assert catalog.historical_values_for_source(
            ResponseValueSource(
                producer_operation_key="GET /producer",
                status_code=status_code,
                media_type=media_type,
                selector="$.value",
                field_name="value",
            )
        ) == []


def test_registration_rejects_sources_without_observed_values() -> None:
    import pytest

    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueUnavailableError,
        ResponseValueTracker,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    ir = _response_value_ir()
    arguments = {
        "ir": ir,
        "consumer_operation_key": "GET /profiles/{userId}",
        "consumer_input_node_id": "node-without-source",
        "parameter_name": "unrelatedToken",
        "expected_type": "string",
    }

    assert tracker.preview(**arguments) is None
    with pytest.raises(ResponseValueUnavailableError) as raised:
        tracker.register(**arguments)
    assert raised.value.code == "response_value_pool_unavailable"
    assert catalog.list_active_monitors() == []


def test_empty_backfill_rolls_back_monitor_and_source_atomically() -> None:
    import pytest

    from restscope.agent.api_behavior_monitor import (
        ResponseValueCatalogRegistration,
        ResponseValueSource,
    )

    catalog = _catalog()
    with pytest.raises(ValueError, match="compatible values"):
        catalog.register_with_backfill(
            ResponseValueCatalogRegistration(
                value_name="response_empty",
                consumer_operation_key="GET /consumer",
                consumer_input_node_id="query/value",
                parameter_name="value",
                expected_type="string",
            ),
            [
                ResponseValueSource(
                    producer_operation_key="GET /producer",
                    status_code="200",
                    media_type="application/json",
                    selector="$.value",
                    field_name="value",
                )
            ],
        )

    assert catalog.list_active_monitors() == []
    assert catalog.list_sources_for_operation("GET /producer") == []


def test_preview_rejects_observed_values_incompatible_with_consumer_type() -> None:
    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    tracker.observe(
        producer_operation_key="GET /users",
        status_code=200,
        media_type="application/json",
        body={"data": [{"user_id": "not-an-integer"}]},
    )

    assert tracker.preview(
        ir=_response_value_ir(),
        consumer_operation_key="GET /profiles/{userId}",
        consumer_input_node_id="path/userId",
        parameter_name="userId",
        expected_type="integer",
    ) is None


def test_response_values_are_typed_and_boolean_is_not_an_integer() -> None:
    from restscope.agent.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalogRegistration,
        ResponseValueSource,
    )

    catalog = _catalog()
    monitor = catalog.ensure_monitor(
        ResponseValueCatalogRegistration(
            value_name="response_types",
            consumer_operation_key="GET /consumer",
            consumer_input_node_id="node",
            parameter_name="value",
            expected_type=None,
        )
    )
    catalog.add_sources(
        monitor.monitor_id,
        [
            ResponseValueSource(
                producer_operation_key="GET /producer",
                status_code="200",
                media_type="application/json",
                selector="$.value",
                field_name="value",
            )
        ],
    )

    assert catalog.record_values(
        monitor.monitor_id,
        ["1", 1, True, 1.5, None, {"nested": "ignored"}],
    ) == 4
    assert catalog.values_for("response_types") == ["1", 1, True, 1.5]


def test_semantic_source_selection_uses_bounded_ir_metadata_only() -> None:
    import json

    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )
    from restscope.llm import LLMModelConfig, LLMResponse

    class StubClient:
        def __init__(self) -> None:
            self.requests = []

        def invoke(self, request):
            self.requests.append(request)
            return LLMResponse(
                provider=request.provider,
                model=request.model,
                parsed_json={"candidate_ids": ["c1"]},
            )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "semantic", "version": "1"},
            "paths": {
                "/commits": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "commits",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "sha": {
                                                    "type": "string",
                                                    "description": (
                                                        "Commit object identifier"
                                                    ),
                                                },
                                                "message": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
                "/builds/{commitId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "commitId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "build"}},
                    }
                },
            },
        }
    )
    client = StubClient()
    tracker = ResponseValueTracker(
        catalog=_catalog(),
        client=client,
        model=LLMModelConfig(
            role="api_behavior_monitor",
            provider="stub",
            model="fast-stub",
        ),
    )
    tracker.observe(
        producer_operation_key="GET /commits",
        status_code=200,
        media_type="application/json",
        body={"sha": "abc123"},
    )
    options = tracker.available_source_options(
        ir=ir,
        consumer_operation_key="GET /builds/{commitId}",
        consumer_input_node_id="commit-input",
        expected_type="string",
    )
    assert [option.source.field_name for option in options] == ["sha"]
    assert client.requests == []

    result = tracker.register(
        ir=ir,
        consumer_operation_key="GET /builds/{commitId}",
        consumer_input_node_id="commit-input",
        parameter_name="commitId",
        expected_type="string",
    )

    assert [source.field_name for source in result.sources] == ["sha"]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.metadata["role"] == "api_behavior_monitor"
    payload = json.loads(request.messages[1].content)
    assert set(payload) == {"consumer", "candidates"}
    assert payload["consumer"] == {
        "parameter_name": "commitId",
        "expected_type": "string",
    }
    assert payload["candidates"][0] == {
        "candidate_id": "c1",
        "producer_operation_key": "GET /commits",
        "status_code": "200",
        "media_type": "application/json",
        "field_path": "$.sha",
        "field_name": "sha",
        "type": "string",
        "format": None,
        "description": "Commit object identifier",
    }


def test_newly_materialized_success_schema_supports_late_registration() -> None:
    from restscope.agent.api_behavior_monitor import ResponseContractTracker
    from restscope.agent.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "refresh", "version": "1"},
            "paths": {
                "/sessions": {
                    "post": {
                        "responses": {
                            "2XX": {
                                "description": "session",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {},
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
                "/events": {
                    "get": {
                        "parameters": [
                            {
                                "name": "sessionToken",
                                "in": "query",
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "events"}},
                    }
                },
            },
        }
    )
    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    ResponseContractTracker().observe(
        ir=ir,
        operation_key="POST /sessions",
        status_code=201,
        media_type="application/json",
        body=b'{"session_token":"token-1"}',
    )
    observed = tracker.observe(
        producer_operation_key="POST /sessions",
        status_code=201,
        media_type="application/json",
        body={"session_token": "token-1"},
    )
    registration = tracker.register(
        ir=ir,
        consumer_operation_key="GET /events",
        consumer_input_node_id="session-token-input",
        parameter_name="sessionToken",
        expected_type="string",
    )

    assert observed.values_recorded == 0
    assert [source.selector for source in registration.sources] == [
        "$.session_token"
    ]
    assert catalog.values_for(registration.value_name) == ["token-1"]
