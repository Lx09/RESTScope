"""End-to-end response intake through the public API Behavior Monitor seam."""

from __future__ import annotations

from datetime import UTC, datetime


def _runtime():
    """Build real Monitor and OpenAPI persistence over one in-memory database."""
    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )
    coordinator = APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(catalog),
        catalog=catalog,
    )
    return coordinator, catalog


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
    from restscope.target_api import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )

    coordinator, catalog = _runtime()
    ir = _ir()
    catalog.initialize_api(
        document=build_openapi_document(ir, list(ir.operations)),
        operations=[],
    )
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
    from restscope.target_api import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )

    class BrokenContractTracker:
        """Represent an internal Contract Monitor defect, not a target failure."""

        def observe(self, **_arguments):
            raise RuntimeError("internal contract defect")

    _coordinator, catalog = _runtime()
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


def test_non_success_text_response_is_saved_without_becoming_learning_evidence() -> None:
    """A 404 Test Case is durable while successful-JSON queries ignore it."""
    from restscope.target_api import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )

    coordinator, catalog = _runtime()
    result = coordinator.observe_response(
        TargetResponseObservation(
            method="GET",
            path="/items",
            url="https://example.test/items",
            status_code=404,
            reason_phrase="Not Found",
            headers={
                "content-type": "text/plain; charset=utf-8",
                "set-cookie": "session=secret",
            },
            body=b"missing",
            body_truncated=False,
            request_json={"path": "/items", "headers": {}},
        ),
        TargetResponseOperationContext(ir=_ir(), operation_key="GET /items"),
    )

    saved = catalog.get_observation(result.observation_id or "")

    assert saved is not None
    assert saved.status_code == 404
    assert saved.body_format == "text"
    assert saved.response_body == b"missing"
    assert saved.response_headers == {
        "content-type": "text/plain; charset=utf-8",
        "set-cookie": "session=secret",
    }
    assert catalog.list_observations(operation_id="GET /items") == []


def test_transport_failure_is_saved_without_an_http_status() -> None:
    """A timeout is a durable Test Case even though no response arrived."""
    from restscope.target_api import (
        TargetResponseOperationContext,
        TargetTransportObservation,
    )

    coordinator, catalog = _runtime()
    result = coordinator.observe_transport(
        TargetTransportObservation(
            method="GET",
            path="/items",
            url="https://example.test/items",
            code="request_timeout",
            message="HTTP request timed out",
            request_json={"path": "/items", "headers": {}},
        ),
        TargetResponseOperationContext(ir=_ir(), operation_key="GET /items"),
    )

    saved = catalog.get_observation(result.observation_id or "")

    assert saved is not None
    assert saved.outcome_kind == "transport"
    assert saved.status_code is None
    assert saved.transport_code == "request_timeout"


def test_confirmed_schema_bug_persists_only_after_linked_replay_reproduces() -> None:
    """The full response processor records Primary, Replay, and final reproduced Check."""

    import httpx

    from restscope.agent import SystemAgentResult
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.oracle import BugOracle
    from restscope.api_behavior_monitor.response_processor import APIBehaviorResponseProcessor
    from restscope.openapi_parser import build_openapi_document
    from restscope.target_api import TargetAPIClient, TargetResponseOperationContext, prepare_target_request

    class ConfirmingRunner:
        """Confirm the one response Schema candidate without external model I/O."""

        def __init__(self):
            self.calls = []

        def run_system_agent(self, profile_name, task):
            self.calls.append((profile_name, task))
            return SystemAgentResult(
                session_id="schema-session",
                profile_name=profile_name,
                status="completed",
                output={"confirmed_bug": True, "reason": "id has the wrong type"},
            )

    _old, catalog = _runtime()
    ir = _ir()
    catalog.initialize_api(
        document=build_openapi_document(ir, list(ir.operations)),
        operations=[],
    )
    runner = ConfirmingRunner()
    coordinator = APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(catalog),
        catalog=catalog,
        bug_oracle=BugOracle(catalog=catalog, system_agent_runner=runner),
    )
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"id":"wrong"}',
            request=request,
        )

    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
        response_processor=APIBehaviorResponseProcessor(coordinator),
    )
    client.send(
        prepare_target_request(
            method="GET",
            base_url="https://example.test",
            path="/items",
        ),
        success_body_limit=100,
        failure_body_limit=100,
        response_context=TargetResponseOperationContext(ir=ir),
    )

    observations = catalog.list_observations(operation_id="GET /items")
    assert len(sent) == 2
    assert len(runner.calls) == 1
    assert len(observations) == 2
    replay = observations[0]
    primary = observations[1]
    assert replay.replay_of_observation_id == primary.observation_id
    assert replay.batch_id is None
    assessment = catalog.get_oracle_assessment(primary.observation_id)
    assert assessment is not None
    assert assessment.replay_observation_id == replay.observation_id
    assert assessment.is_bug is True
    assert [check.status for check in assessment.assessment.checks] == [
        "not_applicable",
        "not_applicable",
        "reproduced",
    ]
    assert catalog.get_oracle_assessment(replay.observation_id) is None
