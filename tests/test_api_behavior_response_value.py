"""Regression scenarios for api behavior response value. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from sqlalchemy.pool import StaticPool

from restscope.openapi_parser import OpenAPIParser


def _catalog():
    from restscope.api_behavior_monitor.response_value_catalog import (
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


def test_catalog_lists_distinct_observed_response_field_identities() -> None:
    """Lookup receives metadata only once even when a scalar repeats over time."""
    from restscope.api_behavior_monitor.response_value_catalog import (
        ObservedResponseField,
    )

    catalog = _catalog()
    for value in (7, 9):
        catalog.record_observation(
            operation_key="GET /users",
            status_code=200,
            media_type="application/json",
            scalars=[("$.data[].user_id", value)],
        )
    catalog.record_observation(
        operation_key="GET /users",
        status_code=201,
        media_type="application/json",
        scalars=[("$.created_id", 10)],
    )

    assert catalog.list_observed_response_fields() == [
        ObservedResponseField(
            operation_key="GET /users",
            status_code=200,
            media_type="application/json",
            selector="$.data[].user_id",
        ),
        ObservedResponseField(
            operation_key="GET /users",
            status_code=201,
            media_type="application/json",
            selector="$.created_id",
        ),
    ]


def test_registers_ir_source_extracts_values_and_deduplicates_them() -> None:
    """Scenario: verify that registers ir source extracts values and deduplicates them."""
    from restscope.api_behavior_monitor.response_value import (
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
    """Scenario: verify that observation history flattens all scalars and keeps latest 100."""
    from restscope.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )
    from restscope.api_behavior_monitor.response_value_catalog import (
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


def test_response_value_pool_keeps_only_100_most_recent_typed_values() -> None:
    """A pool deterministically removes its oldest active value at capacity 101."""

    from restscope.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalogRegistration,
    )

    catalog = _catalog()
    catalog.ensure_monitor(
        ResponseValueCatalogRegistration(
            value_name="response_recent",
            consumer_operation_key="GET /consumer",
            consumer_input_node_id="query/token",
            parameter_name="token",
            expected_type="string",
        )
    )

    catalog.record_values(
        "response_recent",
        [f"value-{index:03d}" for index in range(101)],
    )

    assert catalog.values_for("response_recent") == [
        f"value-{index:03d}" for index in range(1, 101)
    ]


def test_1001_scalars_skip_the_whole_response_and_every_pool_update() -> None:
    """Exactly 1000 scalars persist; 1001 returns a warning with no partial evidence."""

    from restscope.api_behavior_monitor.response_value import ResponseValueTracker
    from restscope.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalogRegistration,
        ResponseValueSource,
    )

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    accepted = tracker.observe(
        producer_operation_key="GET /large",
        status_code=200,
        media_type="application/json",
        body={f"field{index}": index for index in range(1000)},
    )
    catalog.ensure_monitor(
        ResponseValueCatalogRegistration(
            value_name="response_large_field",
            consumer_operation_key="GET /consumer",
            consumer_input_node_id="query/value",
            parameter_name="value",
            expected_type="integer",
        )
    )
    catalog.add_sources(
        "response_large_field",
        [
            ResponseValueSource(
                producer_operation_key="GET /large",
                status_code="200",
                media_type="application/json",
                selector="$.field0",
                field_name="field0",
            )
        ],
    )

    skipped = tracker.observe(
        producer_operation_key="GET /large",
        status_code=200,
        media_type="application/json",
        body={f"field{index}": index + 10 for index in range(1001)},
    )

    assert accepted.warning is None
    assert skipped.warning is not None
    assert skipped.warning.code == "response_observation_scalar_limit_exceeded"
    assert catalog.values_for("response_large_field") == []
    assert catalog.historical_values_for_source(
        ResponseValueSource(
            producer_operation_key="GET /large",
            status_code="200",
            media_type="application/json",
            selector="$.field0",
            field_name="field0",
        )
    ) == [0]


def test_response_observation_and_pool_updates_roll_back_as_one_transaction(
    monkeypatch,
) -> None:
    """A pool write failure removes the parent observation and all its scalars."""

    import pytest

    from restscope.api_behavior_monitor.response_value import ResponseValueTracker
    from restscope.api_behavior_monitor.response_value_catalog import (
        ResponseValueCatalogRegistration,
        ResponseValueSource,
    )
    from restscope.db.repositories import SqlAlchemyResponseValueCatalogRepository

    catalog = _catalog()
    tracker = ResponseValueTracker(catalog=catalog)
    catalog.ensure_monitor(
        ResponseValueCatalogRegistration(
            value_name="response_atomic",
            consumer_operation_key="GET /consumer",
            consumer_input_node_id="query/value",
            parameter_name="value",
            expected_type="integer",
        )
    )
    source = ResponseValueSource(
        producer_operation_key="GET /producer",
        status_code="200",
        media_type="application/json",
        selector="$.value",
        field_name="value",
    )
    catalog.add_sources("response_atomic", [source])

    def fail_pool_write(self, value_name, values, *, now):
        del self, value_name, values, now
        raise RuntimeError("simulated pool failure")

    monkeypatch.setattr(
        SqlAlchemyResponseValueCatalogRepository,
        "_record_values",
        fail_pool_write,
    )
    with pytest.raises(RuntimeError, match="simulated pool failure"):
        tracker.observe(
            producer_operation_key="GET /producer",
            status_code=200,
            media_type="application/json",
            body={"value": 7},
        )

    assert catalog.values_for("response_atomic") == []
    assert catalog.historical_values_for_source(source) == []


def test_observation_history_ignores_non_2xx_and_non_json() -> None:
    """Scenario: verify that observation history ignores non successful 2xx  and non json."""
    from restscope.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )
    from restscope.api_behavior_monitor.response_value_catalog import (
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
    """Scenario: verify that registration rejects sources without observed values."""
    import pytest

    from restscope.api_behavior_monitor.response_value import (
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
    assert catalog.list_monitors() == []


def test_empty_backfill_rolls_back_monitor_and_source_atomically() -> None:
    """Scenario: verify that empty backfill rolls back monitor and source atomically."""
    import pytest

    from restscope.api_behavior_monitor.response_value_catalog import ResponseValueCatalogRegistration
    from restscope.api_behavior_monitor import ResponseValueSource

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

    assert catalog.list_monitors() == []
    assert catalog.list_sources_for_operation("GET /producer") == []


def test_preview_rejects_observed_values_incompatible_with_consumer_type() -> None:
    """Scenario: verify that preview rejects observed values incompatible with consumer type."""
    from restscope.api_behavior_monitor.response_value import (
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
    """Scenario: verify that response values are typed and boolean is not an integer."""
    from restscope.api_behavior_monitor.response_value_catalog import (
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
        monitor.value_name,
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
        monitor.value_name,
        ["1", 1, True, 1.5, None, {"nested": "ignored"}],
    ) == 4
    assert catalog.values_for("response_types") == ["1", 1, True, 1.5]


def test_semantic_source_selection_uses_bounded_ir_metadata_only() -> None:
    """Scenario: verify that semantic source selection uses bounded ir metadata only."""
    from restscope.api_behavior_monitor.response_value import (
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
                parsed_json={"sources": ["S1"]},
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
        parameter_name="commitId",
        expected_type="string",
    )
    assert [option.source.field_name for option in options] == ["sha"]
    assert len(client.requests) == 1

    result = tracker.register(
        ir=ir,
        consumer_operation_key="GET /builds/{commitId}",
        consumer_input_node_id="commit-input",
        parameter_name="commitId",
        expected_type="string",
    )

    assert [source.field_name for source in result.sources] == ["sha"]
    assert len(client.requests) == 2
    request = client.requests[1]
    assert request.metadata["role"] == "api_behavior_monitor"
    prompt = request.messages[1].content
    assert "- `P1`" in prompt
    assert 'parameter: "commitId"' in prompt
    assert "- `S1`" in prompt
    assert 'producer: "GET /commits"' in prompt
    assert 'field: "body.sha"' in prompt
    assert "Commit object identifier" in prompt
    assert "string:" not in prompt
    assert request.response_format == "json"
    assert request.json_schema is None
    for forbidden in (
        "candidate_id",
        "selector",
        "$.sha",
        "actual_value",
        "$defs",
    ):
        assert forbidden not in prompt


def test_available_source_options_prefer_backed_exact_name_fields() -> None:
    """Scenario: verify that available source options prefer backed exact name fields."""
    from restscope.api_behavior_monitor.response_value import (
        ResponseValueTracker,
    )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "assignments", "version": "1"},
            "paths": {
                "/assignments": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "assignments",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "collection": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "commitDate": {
                                                                "type": "string"
                                                            },
                                                            "password": {
                                                                "type": "string"
                                                            },
                                                            "employeeId": {
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
                        }
                    }
                }
            },
        }
    )
    tracker = ResponseValueTracker(catalog=_catalog())
    tracker.observe(
        producer_operation_key="GET /assignments",
        status_code=200,
        media_type="application/json",
        body={
            "collection": [
                {
                    "commitDate": "2026-07-25",
                    "password": "visible-target-data",
                    "employeeId": 7,
                }
            ]
        },
    )

    options = tracker.available_source_options(
        ir=ir,
        consumer_operation_key="DELETE /assignments/{employeeId}",
        consumer_input_node_id="employee-input",
        parameter_name="employeeId",
        expected_type=None,
    )

    assert [option.source.field_name for option in options] == ["employeeId"]


def test_semantic_source_selection_fails_closed_without_repair() -> None:
    """Scenario: verify that semantic source selection fails closed without repair."""
    from restscope.api_behavior_monitor.response_value import (
        ResponseValueTracker,
        _SourceCandidate,
    )
    from restscope.api_behavior_monitor.response_value_catalog import (
        ResponseValueSource,
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
                parsed_json={"sources": ["S9"]},
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

    selected = tracker._semantic_sources(
        parameter_name="commitId",
        expected_type="string",
        candidates=[
            _SourceCandidate(
                source=ResponseValueSource(
                    producer_operation_key="GET /commits",
                    status_code="200",
                    media_type="application/json",
                    selector="$.sha",
                    field_name="sha",
                ),
                field_type="string",
                schema_format=None,
                description=None,
            )
        ],
    )

    assert selected == []
    assert len(client.requests) == 1


def test_newly_materialized_success_schema_supports_late_registration() -> None:
    """Scenario: verify that newly materialized success schema supports late registration."""
    from restscope.api_behavior_monitor.contract_tracker import ResponseContractTracker
    from restscope.api_behavior_monitor.response_value import (
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
