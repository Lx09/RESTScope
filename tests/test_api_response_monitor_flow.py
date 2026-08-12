"""End-to-end response intake through the public API Behavior Monitor seam."""

from __future__ import annotations

from datetime import UTC, datetime


def _runtime():
    """Build real Monitor and OpenAPI persistence over one in-memory database."""
    from restscope.api_behavior_monitor.catalog import ResponseMonitorCatalog
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.response_contracts import ResponseContractTracker
    from restscope.db import (
        Base,
        SqlAlchemyOpenAPIUnitOfWork,
        SqlAlchemyResponseMonitorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_audit import OpenAPIAudit

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = ResponseMonitorCatalog(
        lambda: SqlAlchemyResponseMonitorUnitOfWork(sessions)
    )
    audit = OpenAPIAudit(lambda: SqlAlchemyOpenAPIUnitOfWork(sessions))
    coordinator = APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(audit),
        catalog=catalog,
    )
    return coordinator, catalog, audit


def _ir():
    """Parse one operation whose successful response is valid JSON."""
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Monitor", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "description": "List items",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
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


def test_valid_success_response_is_saved_even_when_resource_derivation_is_absent() -> None:
    """Observation is the independent fact boundary after contract checking."""
    from restscope.openapi_parser import build_openapi_document
    from restscope.target_http import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )

    coordinator, catalog, audit = _runtime()
    ir = _ir()
    audit.initialize(build_openapi_document(ir, list(ir.operations)))
    from restscope.api_behavior_monitor.catalog import (
        AbstractTestCaseWrite,
        OperationDefinition,
    )

    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /items",
            method="GET",
            path="/items",
        )
    )
    abstract = catalog.ensure_abstract_test_case(
        AbstractTestCaseWrite(
            operation_id="GET /items",
            state_digest="b" * 64,
            generators_json={"configs": []},
            constraints_json={"constraints": []},
        )
    )
    received_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
    result = coordinator.observe_response(
        TargetResponseObservation(
            method="GET",
            path="/items",
            url="https://example.test/items?tag=a&tag=b",
            status_code=200,
            reason_phrase="OK",
            headers={"content-type": "application/json; charset=utf-8"},
            body=b'{ "id" : 7 }',
            body_truncated=False,
            received_at=received_at,
            request_json={
                "path": "/items",
                "query": [["tag", "a"], ["tag", "b"]],
                "headers": {"accept": "application/json"},
            },
        ),
        TargetResponseOperationContext(
            ir=ir,
            operation_key="GET /items",
            abstract_test_case_id=abstract.abstract_test_case_id,
        ),
    )

    saved = catalog.list_observations(operation_id="GET /items")
    assert result.observation_id == saved[0].observation_id
    assert saved[0].timestamp == received_at
    assert saved[0].response_json == '{ "id" : 7 }'
    assert saved[0].request_json["query"] == [["tag", "a"], ["tag", "b"]]
    assert saved[0].abstract_test_case_id == abstract.abstract_test_case_id


def test_internal_contract_failure_warns_but_does_not_block_valid_observation() -> None:
    """Contract Monitor availability is independent from factual response intake."""

    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.target_http import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )

    class BrokenContractTracker:
        """Represent an internal Contract Monitor defect, not a target failure."""

        def observe(self, **_arguments):
            raise RuntimeError("internal contract defect")

    _coordinator, catalog, _audit = _runtime()
    coordinator = APIBehaviorMonitorCoordinator(
        contract_tracker=BrokenContractTracker(),
        catalog=catalog,
    )
    result = coordinator.observe_response(
        TargetResponseObservation(
            method="GET",
            path="/items",
            url="https://example.test/items",
            status_code=200,
            reason_phrase="OK",
            headers={"content-type": "application/json"},
            body=b'{"id": 9}',
            body_truncated=False,
            request_json={"path": "/items", "headers": {}},
        ),
        TargetResponseOperationContext(ir=_ir(), operation_key="GET /items"),
    )

    assert result.observation_id is not None
    assert [item.code for item in result.warnings] == [
        "response_contract_check_failed"
    ]
    assert len(catalog.list_observations(operation_id="GET /items")) == 1
