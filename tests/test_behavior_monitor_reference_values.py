"""On-demand Generator values from the redesigned Response Monitor facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _catalog():
    """Create one real in-memory persistence boundary for provider scenarios."""

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
    return APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )


def test_response_values_are_parsed_from_exact_observations_on_demand() -> None:
    """No shared pool is needed, and mismatched response coordinates stay out."""

    from restscope.api_behavior_monitor.catalog import (
        ObservationWrite,
        OperationDefinition,
    )
    from restscope.request_generation import (
        BehaviorMonitorReferenceValues,
    )
    from restscope.request_generation.models import (
        OperationInputSourceReference,
        ResponseValueGenerator,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /items",
            method="GET",
            path="/items",
        )
    )
    now = datetime(2026, 8, 11, tzinfo=UTC)
    for status_code, response_json in (
        (200, '{"items":[{"id":7},{"id":true},{"id":7}]}'),
        (201, '{"items":[{"id":99}]}'),
    ):
        catalog.record_observation(
            ObservationWrite(
                operation_id="GET /items",
                timestamp=now,
                status_code=status_code,
                media_type="application/json",
                request_json={"path": "/items"},
                response_json=response_json,
            )
        )
    strategy = ResponseValueGenerator(
        type="response_value",
        source=OperationInputSourceReference(
            producer_operation_id="GET /items",
            status_code=200,
            media_type="application/json; charset=utf-8",
            selector="$.items[].id",
            field_name="id",
        ),
    )

    values = BehaviorMonitorReferenceValues(catalog).values_for(strategy)

    # Boolean true and integer one compare equal in Python, so retaining type
    # in the de-duplication key is part of the JSON evidence contract.
    assert values == (7, True)
    assert {
        (item.operation_key, item.status_code, item.media_type)
        for item in catalog.list_observed_response_coordinates()
    } == {
        ("GET /items", 200, "application/json"),
        ("GET /items", 201, "application/json"),
    }


def test_response_values_keep_only_eight_latest_distinct_candidates() -> None:
    """VALUE_REUSE stops after eight newest values from its exact coordinate."""
    from restscope.api_behavior_monitor.catalog import (
        ObservationWrite,
        OperationDefinition,
    )
    from restscope.request_generation import (
        BehaviorMonitorReferenceValues,
    )
    from restscope.request_generation.models import (
        OperationInputSourceReference,
        ResponseValueGenerator,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /items",
            method="GET",
            path="/items",
        )
    )
    started = datetime(2026, 8, 11, tzinfo=UTC)
    for index in range(12):
        catalog.record_observation(
            ObservationWrite(
                operation_id="GET /items",
                timestamp=started + timedelta(seconds=index),
                status_code=200,
                media_type="application/json",
                request_json={"path": "/items"},
                response_json=f'{{"item":{{"id":{index}}}}}',
            )
        )
    # A newer response at another exact coordinate must not displace matching
    # values before the provider applies its eight-value bound.
    catalog.record_observation(
        ObservationWrite(
            operation_id="GET /items",
            timestamp=started + timedelta(seconds=20),
            status_code=201,
            media_type="application/json",
            request_json={"path": "/items"},
            response_json='{"item":{"id":99}}',
        )
    )
    strategy = ResponseValueGenerator(
        type="response_value",
        source=OperationInputSourceReference(
            producer_operation_id="GET /items",
            status_code=200,
            media_type="application/json",
            selector="$.item.id",
            field_name="id",
        ),
    )

    values = BehaviorMonitorReferenceValues(catalog).values_for(strategy)

    assert values == (11, 10, 9, 8, 7, 6, 5, 4)


def test_staged_source_commit_failure_rolls_back_flushed_database_rows() -> None:
    """A database commit error leaves neither its operation nor source row."""
    import pytest

    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        OperationInputSource,
        APIBehaviorCatalog,
    )
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    class CommitFailureUnitOfWork(SqlAlchemyAPIBehaviorUnitOfWork):
        """Fail after repository flushes but before SQLAlchemy commits."""

        def commit(self) -> None:
            raise RuntimeError("database commit failed")

    catalog = APIBehaviorCatalog(lambda: CommitFailureUnitOfWork(sessions))
    consumer = OperationDefinition(
        operation_id="GET /consumers",
        method="GET",
        path="/consumers",
    )
    producer = OperationDefinition(
        operation_id="GET /items",
        method="GET",
        path="/items",
    )
    source = OperationInputSource(
        consumer_operation_id=consumer.operation_id,
        consumer_input_node_id="query.item_id",
        consume_type="VALUE_REUSE",
        producer_operation_id=producer.operation_id,
        status_code=200,
        media_type="application/json",
        selector="$.id",
        field_name="id",
    )

    with pytest.raises(RuntimeError, match="database commit failed"):
        with catalog.stage_input_sources(
            operations=[consumer, producer],
            sources=[source],
        ):
            pass

    readable = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )
    assert readable.list_input_sources(
        consumer_operation_id=consumer.operation_id,
        consumer_input_node_id=source.consumer_input_node_id,
    ) == []


def test_resource_source_returns_complete_correlated_current_instances() -> None:
    """Two identity fields resolve through one operation-resource edge."""

    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        ResourceDerivation,
    )
    from restscope.request_generation import (
        BehaviorMonitorReferenceValues,
    )
    from restscope.request_generation.models import (
        OperationInputSourceReference,
        ResourceIdentifierGenerator,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /memberships",
            method="GET",
            path="/memberships",
        )
    )
    catalog.record_resource_derivations(
        operation_id="GET /memberships",
        derivations=[
            ResourceDerivation(
                resource_name="memberships",
                identity_fields=["organization_id", "user_id"],
                role="REFERENCED",
                instances=[
                    {"organization_id": "acme", "user_id": 42},
                    {"organization_id": "globex", "user_id": 77},
                ],
            )
        ],
    )
    source = OperationInputSourceReference(
        producer_operation_id="GET /memberships",
        status_code=200,
        media_type="application/json",
        selector="$.items[].organization_id",
        field_name="organization_id",
    )
    strategy = ResourceIdentifierGenerator(
        type="resource_identifier",
        source=source,
    )
    provider = BehaviorMonitorReferenceValues(catalog)

    assert provider.resource_key(strategy) == "memberships"
    assert list(provider.resource_records(strategy)) == [
        {
            "organization_id": "acme",
            "user_id": 42,
            "_deleted": False,
        },
        {
            "organization_id": "globex",
            "user_id": 77,
            "_deleted": False,
        },
    ]


def test_patch_staging_persists_only_the_exact_consumer_source_proposition() -> None:
    """Publishing a VALUE_REUSE Generator writes no copied producer values."""

    import pytest

    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        BehaviorMonitorReferenceValues,
        RequestGenerationConfigStore,
    )
    from restscope.request_generation.models import (
        OperationInputSourceReference,
        ResponseValueGenerator,
    )
    from restscope.request_generation.store import ReferenceValueBinding

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Sources", "version": "1"},
            "paths": {
                "/consumers": {
                    "get": {
                        "parameters": [
                            {
                                "name": "item_id",
                                "in": "query",
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    config = store.require_state("GET /consumers").config
    input_node_id = config.snapshot.parameters[0].input_node_id
    source = OperationInputSourceReference(
        producer_operation_id="GET /items",
        status_code=200,
        media_type="application/json",
        selector="$.items[].id",
        field_name="id",
    )
    binding = ReferenceValueBinding(
        input_node_id=input_node_id,
        kind="response_value",
        producer_operation_id=source.producer_operation_id,
        status_code=source.status_code,
        media_type=source.media_type,
        selector=source.selector,
        field_name=source.field_name,
    )
    catalog = _catalog()
    provider = BehaviorMonitorReferenceValues(catalog)

    with pytest.raises(RuntimeError, match="publication failed"):
        with provider.stage_bindings(
            config=config,
            bindings=(binding,),
        ):
            raise RuntimeError("publication failed")
    assert catalog.list_input_sources(
        consumer_operation_id="GET /consumers",
        consumer_input_node_id=input_node_id,
    ) == []

    with provider.stage_bindings(config=config, bindings=(binding,)):
        pass

    rows = catalog.list_input_sources(
        consumer_operation_id="GET /consumers",
        consumer_input_node_id=input_node_id,
    )
    assert len(rows) == 1
    assert rows[0].consume_type == "VALUE_REUSE"
    assert rows[0].alpha == rows[0].beta == 1
