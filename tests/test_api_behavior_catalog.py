"""Public persistence behavior for the unified API Behavior Catalog."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _catalog():
    """Create one real in-memory database behind the public Monitor Catalog."""
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


def test_api_behavior_catalog_initializes_document_and_operations_atomically() -> None:
    """One API initialization publishes its document and operation metadata together."""

    from restscope.api_behavior_monitor import APIBehaviorCatalog
    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(make_session_factory(engine))
    )
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Catalog", "version": "1"},
        "paths": {
            "/items": {
                "get": {"responses": {"200": {"description": "ok"}}}
            }
        },
    }
    operation = OperationDefinition(
        operation_id="GET /items",
        method="GET",
        path="/items",
    )

    catalog.initialize_api(document=document, operations=[operation])

    assert catalog.current_openapi() == document
    assert catalog.baseline_openapi() == document
    assert catalog.list_openapi_changes() == []
    assert catalog.list_operation_resources(operation_id="GET /items") == []


def test_current_openapi_changes_without_mutating_oracle_baseline() -> None:
    """Contract evolution changes only the current document used by Monitor."""

    from restscope.api_behavior_monitor.catalog import OpenAPIChangeEventWrite

    catalog = _catalog()
    original = {"openapi": "3.0.3", "info": {"title": "A", "version": "1"}, "paths": {}}
    evolved = {"openapi": "3.0.3", "info": {"title": "B", "version": "1"}, "paths": {}}
    catalog.initialize_api(document=original, operations=[])

    catalog.record_openapi_change(
        document=evolved,
        event=OpenAPIChangeEventWrite(
            operation_key="GET /items",
            status_code=500,
            changes=["response:500"],
            response_after={"description": "observed"},
        ),
    )

    assert catalog.current_openapi() == evolved
    assert catalog.baseline_openapi() == original


def test_api_behavior_catalog_rejects_reinitialization_without_partial_changes() -> None:
    """A second initialization cannot add operations or replace the first document."""

    import pytest

    from restscope.api_behavior_monitor import APIBehaviorCatalog
    from restscope.api_behavior_monitor.catalog import OperationDefinition
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(make_session_factory(engine))
    )
    original = {
        "openapi": "3.0.3",
        "info": {"title": "First", "version": "1"},
        "paths": {},
    }
    catalog.initialize_api(document=original, operations=[])

    with pytest.raises(ValueError, match="already initialized"):
        catalog.initialize_api(
            document={
                "openapi": "3.0.3",
                "info": {"title": "Second", "version": "1"},
                "paths": {},
            },
            operations=[
                OperationDefinition(
                    operation_id="GET /unexpected",
                    method="GET",
                    path="/unexpected",
                )
            ],
        )

    assert catalog.current_openapi() == original


def test_catalog_persists_complete_batches_and_all_http_outcomes() -> None:
    """Every HTTP case remains queryable in its original Batch order."""
    from restscope.api_behavior_monitor.catalog import (
        BatchWrite,
        ObservationWrite,
        OperationDefinition,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /items",
            method="GET",
            path="/items",
            description="List items",
        )
    )
    batch = catalog.create_batch(
        BatchWrite(summary={"schema_version": 1, "status": "running"})
    )
    started = datetime(2026, 8, 11, tzinfo=UTC)
    for index in range(101):
        catalog.record_observation(
            ObservationWrite(
                operation_id="GET /items",
                timestamp=started + timedelta(seconds=index),
                outcome_kind="http",
                status_code=404 if index == 100 else 200,
                reason_phrase="Not Found" if index == 100 else "OK",
                media_type="application/json" if index < 100 else "text/plain",
                request_json={"path": "/items", "query": []},
                response_headers={"set-cookie": "session=secret"},
                response_body=(
                    f'{{ "position" : {index} }}'.encode()
                    if index < 100
                    else b"missing"
                ),
                body_format="json" if index < 100 else "text",
                batch_id=batch.batch_id,
                batch_case_index=index,
            )
        )

    observations, total = catalog.list_batch_observations(
        batch_id=batch.batch_id,
        offset=0,
        limit=200,
    )

    assert total == 101
    assert len(observations) == 101
    assert observations[0].batch_case_index == 0
    assert observations[-1].status_code == 404
    assert observations[-1].response_body == b"missing"
    assert observations[-1].response_headers == {"set-cookie": "session=secret"}
    assert all(item.operation_id == "GET /items" for item in observations)


def test_catalog_persists_transport_test_cases_without_an_http_status() -> None:
    """A sent request with no response remains an unambiguous Test Case."""
    from restscope.api_behavior_monitor.catalog import (
        ObservationWrite,
        OperationDefinition,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /items",
            method="GET",
            path="/items",
        )
    )

    saved = catalog.record_observation(
        ObservationWrite(
            operation_id="GET /items",
            timestamp=datetime(2026, 8, 12, tzinfo=UTC),
            outcome_kind="transport",
            request_json={"path": "/items", "query": []},
            transport_code="request_timeout",
            transport_message="HTTP request timed out",
        )
    )

    restored = catalog.get_observation(saved.observation_id)

    assert restored == saved
    assert restored is not None
    assert restored.status_code is None
    assert restored.response_body is None


def test_catalog_persists_one_replay_and_immutable_oracle_assessment() -> None:
    """A Primary owns at most one same-operation Replay and one final verdict."""

    import pytest

    from restscope.api_behavior_monitor.catalog import (
        ObservationWrite,
        OperationDefinition,
        OracleAssessment,
        OracleCheckNoCandidate,
        OracleCheckNotApplicable,
        OracleCheckReproduced,
    )

    catalog = _catalog()
    catalog.ensure_operation(OperationDefinition(operation_id="GET /items", method="GET", path="/items"))
    common = dict(
        operation_id="GET /items",
        timestamp=datetime(2026, 8, 13, tzinfo=UTC),
        outcome_kind="http",
        request_json={"path": "/items", "query": []},
        status_code=500,
        response_headers={"content-type": "application/json"},
        response_body=b'{}',
        body_format="json",
    )
    primary = catalog.record_observation(ObservationWrite(**common))
    replay = catalog.record_observation(
        ObservationWrite(**common, replay_of_observation_id=primary.observation_id)
    )
    assessment = OracleAssessment(
        checks=(
            OracleCheckReproduced(
                name="valid_input_server_error",
                status="reproduced",
                agent_session_id="session-1",
                reason="Valid input repeatedly received a server error.",
            ),
            OracleCheckNotApplicable(name="invalid_input_accepted", status="not_applicable"),
            OracleCheckNoCandidate(name="response_schema_mismatch", status="no_candidate"),
        )
    )

    saved = catalog.record_oracle_assessment(
        primary_observation_id=primary.observation_id,
        replay_observation_id=replay.observation_id,
        assessment=assessment,
    )

    assert saved.is_bug is True
    assert saved.assessment == assessment
    assert catalog.get_oracle_assessment(primary.observation_id) == saved
    with pytest.raises(ValueError, match="immutable"):
        catalog.record_oracle_assessment(
            primary_observation_id=primary.observation_id,
            replay_observation_id=replay.observation_id,
            assessment=assessment,
        )
    with pytest.raises(ValueError, match="Replay lineage"):
        catalog.record_observation(
            ObservationWrite(**common, replay_of_observation_id=primary.observation_id)
        )


def test_composite_resource_instances_merge_nested_state_without_null_overwrite() -> None:
    """All identity components stay correlated while later state updates merge."""
    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        ResourceDerivation,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="PATCH /memberships/{organizationId}/{userId}",
            method="PATCH",
            path="/memberships/{organizationId}/{userId}",
        )
    )
    first = ResourceDerivation(
        resource_name="Memberships",
        identity_fields=["organization_id", "user_id"],
        role="UPDATED",
        instances=[
            {
                "organization_id": "acme",
                "user_id": 42,
                "profile": {"name": "Lin", "email": "old@example.test"},
                "tags": ["member"],
            }
        ],
    )
    second = ResourceDerivation(
        resource_name="memberships",
        identity_fields=["user_id", "organization_id"],
        role="UPDATED",
        instances=[
            {
                "organization_id": "acme",
                "user_id": 42,
                "profile": {"name": None, "email": "new@example.test"},
                "tags": ["admin"],
            }
        ],
    )

    catalog.record_resource_derivations(
        operation_id="PATCH /memberships/{organizationId}/{userId}",
        derivations=[first, second],
    )

    resources, total = catalog.list_resources(offset=0, limit=10)
    instances, instance_total = catalog.list_resource_instances(
        resource_type="memberships",
        offset=0,
        limit=10,
    )
    assert total == 1
    assert resources[0].identity_fields == ("organization_id", "user_id")
    assert instance_total == 1
    assert instances[0].resource_instance_id == (
        '{"organization_id":"acme","user_id":42}'
    )
    assert instances[0].current_state_json == {
        "organization_id": "acme",
        "user_id": 42,
        "profile": {"name": "Lin", "email": "new@example.test"},
        "tags": ["admin"],
        "_deleted": False,
    }


def test_identical_source_coordinates_can_keep_both_consumer_meanings() -> None:
    """RESOURCE and VALUE_REUSE are distinct propositions with neutral priors."""
    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        OperationInputSource,
    )

    catalog = _catalog()
    for operation in (
        OperationDefinition(
            operation_id="GET /users",
            method="GET",
            path="/users",
        ),
        OperationDefinition(
            operation_id="GET /profiles/{userId}",
            method="GET",
            path="/profiles/{userId}",
        ),
    ):
        catalog.ensure_operation(operation)
    common = {
        "consumer_operation_id": "GET /profiles/{userId}",
        "consumer_input_node_id": "path:userId",
        "producer_operation_id": "GET /users",
        "status_code": 200,
        "media_type": "application/json; charset=utf-8",
        "selector": "$.items[].user_id",
        "field_name": "user_id",
    }

    resource = catalog.ensure_input_source(
        OperationInputSource(**common, consume_type="RESOURCE")
    )
    value_reuse = catalog.ensure_input_source(
        OperationInputSource(**common, consume_type="VALUE_REUSE")
    )

    assert resource.consume_type == "RESOURCE"
    assert value_reuse.consume_type == "VALUE_REUSE"
    assert resource.media_type == "application/json"
    assert resource.alpha == resource.beta == 1
    assert value_reuse.alpha == value_reuse.beta == 1
    assert len(
        catalog.list_input_sources(
            consumer_operation_id="GET /profiles/{userId}",
            consumer_input_node_id="path:userId",
        )
    ) == 2


def test_abstract_test_case_reuses_one_operation_state_digest() -> None:
    """Repeated Batches using the same immutable state share one audit identity."""
    from restscope.api_behavior_monitor.catalog import (
        AbstractTestCaseWrite,
        OperationDefinition,
    )

    catalog = _catalog()
    catalog.ensure_operation(
        OperationDefinition(
            operation_id="POST /items",
            method="POST",
            path="/items",
        )
    )
    draft = AbstractTestCaseWrite(
        operation_id="POST /items",
        state_digest="a" * 64,
        generators_json={
            "active_media_type": "application/json",
            "inputs": [
                {
                    "input_node_id": "body:name",
                    "inclusion_probability": 1.0,
                    "strategy": {"type": "constant", "value": "Ada"},
                }
            ],
        },
        constraints_json={"constraints": []},
    )

    first = catalog.ensure_abstract_test_case(draft)
    second = catalog.ensure_abstract_test_case(draft)

    assert first.abstract_test_case_id == second.abstract_test_case_id
    assert first.operation_id == "POST /items"
    assert first.generators_json == draft.generators_json
    assert first.constraints_json == {"constraints": []}
