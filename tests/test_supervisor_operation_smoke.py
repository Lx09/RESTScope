"""Regression scenarios for supervisor operation smoke. Each test documents one observable contract or failure boundary."""

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
        result_status = (
            "retry"
            if status in {"no_new_failure_work", "plan_budget_exhausted"}
            else status
        )
        reports = []
        if result_status == "passed":
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
            status=result_status,
            operation_key=request.operation_key,
            success_rate=1.0 if result_status == "passed" else 0.0,
            required_success_rate=request.success_rate_threshold,
            active_config_revision=1,
            batch_reports=reports,
            failure_kind=(
                status
                if status in {
                    "no_new_failure_work",
                    "plan_budget_exhausted",
                }
                else "no_new_failure_work"
                if result_status == "retry"
                else "unsupported_operation"
                if result_status == "unsupported"
                else "operation_error"
                if result_status == "errored"
                else None
            ),
            error=(
                {"type": "SmokeError", "message": "local operation failure"}
                if result_status == "errored"
                else None
            ),
        )


def test_supervisor_uses_smoke_agent_without_exposing_successful_operations() -> None:
    """Scenario: verify that supervisor uses smoke agent without exposing successful operations."""
    from restscope.agent import OperationAttempt, RESTScopeMainGraph, RESTScopeRunReport, RESTScopeRunRequest

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
    assert all(
        "successful_operation_keys" not in request.model_dump()
        for request in smoke.requests
    )
    assert [attempt.smoke_result.status for attempt in report.attempts] == [
        "passed",
        "passed",
    ]
    assert list(OperationAttempt.model_fields) == [
        "operation",
        "round_number",
        "attempt_number",
        "smoke_result",
        "disposition",
        "failure_kind",
    ]
    assert list(RESTScopeRunReport.model_fields) == [
        "report_id",
        "status",
        "stop_reason",
        "operations",
        "attempts",
        "satisfied_operations",
        "unattempted_operations",
        "rounds",
        "attempt_count",
        "error",
        "metadata",
    ]


def test_supervisor_retries_smoke_operation_in_the_next_round() -> None:
    """Scenario: verify that supervisor retries smoke operation in the next round."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["retry", "passed", "passed"])

    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert report.status == "passed", report.model_dump(mode="json")
    assert report.stop_reason == "completed"
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first",
        "POST /second",
        "GET /first",
    ]
    assert [
        (attempt.operation.path, attempt.round_number, attempt.disposition)
        for attempt in report.attempts
    ] == [
        ("/first", 1, "retrying"),
        ("/second", 1, "satisfied"),
        ("/first", 2, "satisfied"),
    ]


def test_supervisor_exhausts_retries_without_interrupting_other_operations() -> None:
    """Scenario: verify that supervisor exhausts retries without interrupting other operations."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["retry", "passed", "retry", "retry"])

    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest(max_operation_attempts=3))

    assert (report.status, report.stop_reason) == (
        "failed",
        "completed_with_failures",
    )
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first",
        "POST /second",
        "GET /first",
        "GET /first",
    ]
    assert [attempt.disposition for attempt in report.attempts] == [
        "retrying",
        "satisfied",
        "retrying",
        "failed",
    ]
    assert [attempt.attempt_number for attempt in report.attempts] == [
        1,
        1,
        2,
        3,
    ]
    assert report.attempts[-1].failure_kind == "no_new_failure_work"
    assert report.unattempted_operations == []


def test_no_new_failure_work_does_not_interrupt_other_operations() -> None:
    """A Plan no-work result remains one operation's retry outcome."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["no_new_failure_work", "passed"])

    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest(max_operation_attempts=1))

    assert (report.status, report.stop_reason) == (
        "failed",
        "completed_with_failures",
    )
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first",
        "POST /second",
    ]
    assert [attempt.disposition for attempt in report.attempts] == [
        "failed",
        "satisfied",
    ]
    assert report.attempts[0].failure_kind == "no_new_failure_work"
    assert report.unattempted_operations == []


def test_unsupported_smoke_operation_does_not_retry_or_stop_following_work() -> None:
    """Scenario: verify that unsupported smoke operation does not retry or stop following work."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["unsupported", "passed"])
    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert (report.status, report.stop_reason) == (
        "failed",
        "completed_with_failures",
    )
    assert [attempt.disposition for attempt in report.attempts] == [
        "unsupported",
        "satisfied",
    ]
    assert report.attempts[0].failure_kind == "unsupported_operation"


def test_operation_scoped_smoke_error_retries_after_other_operations() -> None:
    """Scenario: verify that operation scoped smoke error retries after other operations."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    smoke = _SmokeAgent(["errored", "passed", "passed"])
    report = RESTScopeMainGraph(
        operation_smoke_agent=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert (report.status, report.stop_reason) == ("passed", "completed")
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first",
        "POST /second",
        "GET /first",
    ]
    assert [attempt.disposition for attempt in report.attempts] == [
        "retrying",
        "satisfied",
        "satisfied",
    ]
    assert report.attempts[0].failure_kind == "operation_error"


def test_smoke_runtime_exception_is_a_global_technical_error() -> None:
    """Scenario: verify that smoke runtime exception is a global technical error."""
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest

    class BrokenSmokeAgent:
        def run(self, context, request):
            del context, request
            raise RuntimeError("shared runtime unavailable")

    report = RESTScopeMainGraph(
        operation_smoke_agent=BrokenSmokeAgent(),
        tool_context=_context(),
    ).run(RESTScopeRunRequest())

    assert (report.status, report.stop_reason) == (
        "errored",
        "technical_error",
    )
    assert report.attempts == []
    assert report.attempt_count == 0
    assert report.error == {
        "type": "RuntimeError",
        "message": "shared runtime unavailable",
        "stage": "run_next_operation",
        "operation": {
            "method": "GET",
            "path": "/first",
            "operation_id": "first",
        },
    }
