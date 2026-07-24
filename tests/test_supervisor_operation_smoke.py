from __future__ import annotations

import json


def _context():
    from restscope.capabilities import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Smoke Supervisor", "version": "1"},
        "paths": {
            "/first": {
                "get": {
                    "operationId": "first",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/second": {
                "post": {
                    "operationId": "second",
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }
    return ToolContext(
        ir=OpenAPIParser.parse(spec),
        baseline_schema_source={
            "kind": "inline",
            "format": "json",
            "content": json.dumps(spec),
        },
        base_url="http://localhost:8000",
    )


class _SmokeAgent:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = list(statuses)
        self.requests = []

    def run(self, context, request):
        from restscope.agent.operation_smoke import OperationSmokeResult
        from restscope.testing import OperationExecutionReport

        del context
        self.requests.append(request)
        status = self.statuses.pop(0)
        reports = []
        if status == "passed":
            reports.append(
                OperationExecutionReport(
                    run_id=f"run_{len(self.requests)}",
                    operation_key=request.operation_key,
                    seed=1,
                    config_revision=1,
                    status="completed",
                    cases=[],
                    status_code_counts={"200": 1},
                    error_count=0,
                    observed_2xx=True,
                )
            )
        return OperationSmokeResult(
            status=status,
            operation_key=request.operation_key,
            success_rate=1.0 if status == "passed" else 0.0,
            required_success_rate=request.success_rate_threshold,
            active_config_revision=1,
            batch_reports=reports,
        )


def test_supervisor_uses_smoke_agent_and_passes_successful_operations() -> None:
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["passed", "passed"])

    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert report.status == "passed"
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first",
        "POST /second",
    ]
    assert smoke.requests[0].successful_operation_keys == []
    assert smoke.requests[1].successful_operation_keys == ["GET /first"]
    assert all(
        attempt.report.metadata["agent"] == "operation_smoke_agent"
        for attempt in report.attempts
    )


def test_supervisor_maps_waiting_smoke_result_to_blocked_operation() -> None:
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["waiting", "passed"])

    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert report.status == "failed", report.model_dump(mode="json")
    assert report.stop_reason == "unresolved_dependencies"
    assert report.blocked_operations[0].operation.path == "/first"
    assert report.blocked_operations[0].reason == "unknown_dependency"
