from __future__ import annotations

from dataclasses import dataclass

from restscope.openapi_parser import OpenAPIParser


@dataclass
class _ResourceResult:
    status: str = "updated"
    warning: object | None = None


class _CapturingResourceTracker:
    def __init__(self) -> None:
        self.observations = []

    def observe(self, observation):
        self.observations.append(observation)
        return _ResourceResult()


@dataclass
class _ValueResult:
    sources_processed: int = 1
    values_recorded: int = 1


class _CapturingValueTracker:
    def __init__(self) -> None:
        self.calls = []

    def observe(self, **kwargs):
        self.calls.append(kwargs)
        return _ValueResult()

    def refresh_sources(self, **kwargs):
        return 0


def _ir():
    return OpenAPIParser.parse(
        {
            "openapi": "3.0.0",
            "info": {"title": "behavior", "version": "1"},
            "paths": {
                "/users": {
                    "post": {
                        "responses": {
                            "2XX": {
                                "description": "user",
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
                }
            },
        }
    )


def _observation(status_code: int, body: bytes):
    from restscope.http_transport import TargetResponseObservation

    return TargetResponseObservation(
        method="POST",
        path="/users",
        url="https://api.example.test/users",
        status_code=status_code,
        reason_phrase="Created" if status_code == 201 else "Bad Request",
        headers={"content-type": "application/json; charset=utf-8"},
        body=body,
        body_truncated=False,
    )


def test_agent_updates_ir_before_success_trackers_receive_evidence() -> None:
    from restscope.agent.api_behavior_monitor import (
        APIBehaviorMonitorAgent,
        ResponseContractTracker,
    )
    from restscope.http_transport import TargetResponseOperationContext

    ir = _ir()
    resource_tracker = _CapturingResourceTracker()
    value_tracker = _CapturingValueTracker()
    agent = APIBehaviorMonitorAgent(
        contract_tracker=ResponseContractTracker(),
        resource_identifier_tracker=resource_tracker,
        response_value_tracker=value_tracker,
    )

    result = agent.observe_response(
        _observation(201, b'{"id":7,"name":"Ada"}'),
        TargetResponseOperationContext(
            ir=ir,
            operation_key="POST /users",
            operation_method="POST",
            operation_path="/users",
        ),
    )

    assert result.contract.status == "updated"
    assert len(resource_tracker.observations) == 1
    resource_observation = resource_tracker.observations[0]
    assert {item["name"] for item in resource_observation.response_schema_fields} == {
        "id",
        "name",
    }
    assert resource_observation.body == {"id": 7, "name": "Ada"}
    assert value_tracker.calls == [
        {
            "producer_operation_key": "POST /users",
            "status_code": 201,
            "media_type": "application/json",
            "body": {"id": 7, "name": "Ada"},
        }
    ]


def test_response_processor_keeps_private_monitor_summary() -> None:
    from restscope.agent.api_behavior_monitor import (
        APIBehaviorMonitorAgent,
        APIBehaviorResponseProcessor,
        ResponseContractTracker,
    )
    from restscope.http_transport import TargetResponseOperationContext

    ir = _ir()
    agent = APIBehaviorMonitorAgent(
        contract_tracker=ResponseContractTracker(),
        resource_identifier_tracker=_CapturingResourceTracker(),
        response_value_tracker=_CapturingValueTracker(),
    )

    result = APIBehaviorResponseProcessor(agent).process(
        _observation(201, b'{"id":7,"name":"Ada"}'),
        TargetResponseOperationContext(
            ir=ir,
            operation_key="POST /users",
            operation_method="POST",
            operation_path="/users",
        ),
    )

    assert result.response_validation == "evaluated"
    assert result.details == {
        "operation_key": "POST /users",
        "status_code": 201,
        "media_type": "application/json",
        "contract_status": "updated",
        "contract_changes": [
            "response:201",
            "response:201:schema",
        ],
        "resource_identifier": {
            "status": "updated",
            "groups_processed": 0,
            "identifiers_recorded": 0,
            "warning_code": None,
        },
        "response_values": {
            "sources_processed": 1,
            "values_recorded": 1,
        },
        "warning_codes": [],
    }


def test_non_success_response_only_updates_contract() -> None:
    from restscope.agent.api_behavior_monitor import (
        APIBehaviorMonitorAgent,
        ResponseContractTracker,
    )
    from restscope.http_transport import TargetResponseOperationContext

    ir = _ir()
    resource_tracker = _CapturingResourceTracker()
    value_tracker = _CapturingValueTracker()
    agent = APIBehaviorMonitorAgent(
        contract_tracker=ResponseContractTracker(),
        resource_identifier_tracker=resource_tracker,
        response_value_tracker=value_tracker,
    )

    result = agent.observe_response(
        _observation(400, b'{"message":"invalid"}'),
        TargetResponseOperationContext(ir=ir, operation_key="POST /users"),
    )

    assert result.contract.status == "updated"
    assert "400" in ir.operations["POST /users"].responses.by_status
    assert resource_tracker.observations == []
    assert value_tracker.calls == []


def test_unknown_context_operation_preserves_stable_warning_code() -> None:
    from restscope.agent.api_behavior_monitor import (
        APIBehaviorMonitorAgent,
        APIBehaviorResponseProcessor,
        ResponseContractTracker,
    )
    from restscope.http_transport import TargetResponseOperationContext

    agent = APIBehaviorMonitorAgent(
        contract_tracker=ResponseContractTracker(),
        resource_identifier_tracker=_CapturingResourceTracker(),
        response_value_tracker=_CapturingValueTracker(),
    )

    result = APIBehaviorResponseProcessor(agent).process(
        _observation(200, b'{"id":7}'),
        TargetResponseOperationContext(
            ir=_ir(),
            operation_key="GET /missing",
        ),
    )

    assert result.response_validation == "partial"
    assert [warning.code for warning in result.warnings] == [
        "operation_not_found"
    ]
