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

    assert first.values_recorded == 2
    assert second.values_recorded == 0
    assert catalog.values_for(registration.value_name) == [7, 9]


def test_registration_is_idempotent_and_empty_pool_is_explicit() -> None:
    from restscope.agent.api_behavior_monitor.response_value import (
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

    first = tracker.register(**arguments)
    second = tracker.register(**arguments)

    assert first.value_name == second.value_name
    assert first.sources == []
    assert second.status == "existing"
    assert catalog.values_for(first.value_name) == []


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


def test_newly_materialized_success_schema_refreshes_existing_monitor_sources() -> None:
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
    registration = tracker.register(
        ir=ir,
        consumer_operation_key="GET /events",
        consumer_input_node_id="session-token-input",
        parameter_name="sessionToken",
        expected_type="string",
    )
    assert registration.sources == []

    ResponseContractTracker().observe(
        ir=ir,
        operation_key="POST /sessions",
        status_code=201,
        media_type="application/json",
        body=b'{"session_token":"token-1"}',
    )
    refreshed = tracker.refresh_sources(
        ir=ir,
        producer_operation_key="POST /sessions",
    )
    observed = tracker.observe(
        producer_operation_key="POST /sessions",
        status_code=201,
        media_type="application/json",
        body={"session_token": "token-1"},
    )

    assert refreshed == 1
    assert observed.values_recorded == 1
    assert catalog.values_for(registration.value_name) == ["token-1"]
