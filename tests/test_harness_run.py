"""Protect run-scoped operation ordering, retries, stopping, and reports."""

from __future__ import annotations

import json


def _context():
    """Build two same-depth operations in stable declaration order."""
    from restscope.tools import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Smoke Run Harness", "version": "1"},
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


def _context_with_deep_route_declared_first():
    """Build routes that prove path depth wins over declaration order."""
    from restscope.openapi_parser import OpenAPIParser
    from restscope.tools import ToolContext

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Run Harness ordering", "version": "1"},
        "paths": {
            "/projects/{projectId}/issues": {
                "get": {"responses": {"200": {"description": "ok"}}}
            },
            "/projects": {
                "get": {"responses": {"200": {"description": "ok"}}}
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


class _SmokeCoordinator:
    """Return scripted Smoke outcomes through the real Harness dependency seam."""

    def __init__(self, statuses: list[str]) -> None:
        """Retain ordered outcomes and every request for later assertions."""
        self.statuses = list(statuses)
        self.requests = []

    def run(self, context, request):
        """Return the next result while recording the selected operation."""
        from restscope.operation_smoke import OperationSmokeResult

        del context
        self.requests.append(request)
        status = self.statuses.pop(0)
        passed_stop_reasons = {
            "passed": "success_rate_reached",
            "no_patch_applied": "no_patch_applied",
        }
        result_status = (
            "passed"
            if status in passed_stop_reasons
            else "errored"
            if status in {"failure_resolution_limit_exceeded", "provider_unavailable"}
            else status
        )
        batch_run_ids = (
            [f"run_{len(self.requests)}"]
            if result_status == "passed" or status == "provider_unavailable"
            else []
        )
        return OperationSmokeResult(
            status=result_status,
            operation_key=request.operation_key,
            success_rate=1.0 if result_status == "passed" else 0.0,
            required_success_rate=request.success_rate_threshold,
            stop_reason=passed_stop_reasons.get(status),
            reason=(
                f"The scripted Coordinator stopped because {passed_stop_reasons[status]}."
                if status in passed_stop_reasons
                else None
            ),
            batch_run_ids=batch_run_ids,
            failure_kind=(
                "failure_resolution_limit_exceeded"
                if status == "failure_resolution_limit_exceeded"
                else "provider_unavailable"
                if status == "provider_unavailable"
                else "unsupported_operation"
                if result_status == "unsupported"
                else "operation_error"
                if result_status == "errored"
                else None
            ),
            error=(
                {
                    "type": (
                        "ProviderUnavailableError"
                        if status == "provider_unavailable"
                        else "SmokeError"
                    ),
                    "message": (
                        "provider_unavailable: Model provider remained unavailable "
                        "after 3 SDK retries (HTTP 503)."
                        if status == "provider_unavailable"
                        else "local operation failure"
                    ),
                }
                if result_status == "errored"
                else None
            ),
        )


def test_run_harness_uses_smoke_coordinator_without_exposing_successful_operations() -> None:
    """Scenario: a successful run exposes attempts but no hidden success input."""
    from restscope.harness import (
        OperationAttempt,
        RESTScopeRunReport,
        RESTScopeRunRequest,
        RunHarness,
    )

    smoke = _SmokeCoordinator(["passed", "passed"])

    report = RunHarness(
        operation_smoke_coordinator=smoke,
        tool_context=_context(),
        random_seed=731,
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
        "random_seed",
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
    assert report.random_seed == 731


def test_run_harness_retries_smoke_operation_in_the_next_round() -> None:
    """Scenario: an operation-local failure moves behind the rest of its round."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    smoke = _SmokeCoordinator(["errored", "passed", "passed"])

    report = RunHarness(
        operation_smoke_coordinator=smoke,
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


def test_run_harness_exhausts_retries_without_interrupting_other_operations() -> None:
    """Scenario: retry exhaustion does not prevent another operation succeeding."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    smoke = _SmokeCoordinator(["errored", "passed", "errored", "errored"])

    report = RunHarness(
        operation_smoke_coordinator=smoke,
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
        "errored",
    ]
    assert [attempt.attempt_number for attempt in report.attempts] == [
        1,
        1,
        2,
        3,
    ]
    assert report.attempts[-1].failure_kind == "operation_error"
    assert report.unattempted_operations == []


def test_unsupported_smoke_operation_does_not_retry_or_stop_following_work() -> None:
    """Scenario: an unsupported operation is final but does not stop later work."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    smoke = _SmokeCoordinator(["unsupported", "passed"])
    report = RunHarness(
        operation_smoke_coordinator=smoke,
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
    """Scenario: a local technical result retries after other operations."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    smoke = _SmokeCoordinator(["errored", "passed", "passed"])
    report = RunHarness(
        operation_smoke_coordinator=smoke,
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


def test_provider_unavailable_records_attempt_and_stops_the_entire_run() -> None:
    """One-model capacity exhaustion leaves later operations unattempted."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    smoke = _SmokeCoordinator(["provider_unavailable", "passed"])
    report = RunHarness(
        operation_smoke_coordinator=smoke,
        tool_context=_context(),
    ).run(RESTScopeRunRequest(max_operation_attempts=3))

    assert (report.status, report.stop_reason) == (
        "errored",
        "technical_error",
    )
    assert [request.operation_key for request in smoke.requests] == [
        "GET /first"
    ]
    assert report.attempt_count == 1
    assert report.attempts[0].disposition == "errored"
    assert report.attempts[0].failure_kind == "provider_unavailable"
    assert report.attempts[0].smoke_result.batch_run_ids == ["run_1"]
    assert [operation.path for operation in report.unattempted_operations] == [
        "/second"
    ]
    assert report.error == {
        "type": "ProviderUnavailableError",
        "message": (
            "provider_unavailable: Model provider remained unavailable after "
            "3 SDK retries (HTTP 503)."
        ),
        "stage": "run_next_operation",
        "operation": {
            "method": "GET",
            "path": "/first",
            "operation_id": "first",
        },
    }


def test_smoke_runtime_exception_is_a_global_technical_error() -> None:
    """Scenario: an unexpected Coordinator exception stops the whole run."""
    from restscope.harness import RunHarness, RESTScopeRunRequest

    class BrokenSmokeCoordinator:
        """Raise at the injected Coordinator seam without calling a real target."""

        def run(self, context, request):
            """Simulate shared infrastructure failure before a result exists."""
            del context, request
            raise RuntimeError("shared runtime unavailable")

    report = RunHarness(
        operation_smoke_coordinator=BrokenSmokeCoordinator(),
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


def test_run_harness_orders_shallow_routes_before_deeper_routes() -> None:
    """Scenario: runtime operation ordering is stable and path-depth-first."""
    from restscope.harness import RESTScopeRunRequest, RunHarness

    smoke = _SmokeCoordinator(["passed", "passed"])

    RunHarness(
        operation_smoke_coordinator=smoke,
        tool_context=_context_with_deep_route_declared_first(),
    ).run(RESTScopeRunRequest())

    assert [request.operation_key for request in smoke.requests] == [
        "GET /projects",
        "GET /projects/{projectId}/issues",
    ]


def test_run_harness_does_not_share_retry_state_between_runs() -> None:
    """Scenario: each call starts at round and attempt one with fresh queues."""
    from restscope.harness import RESTScopeRunRequest, RunHarness

    smoke = _SmokeCoordinator(["passed", "passed", "passed", "passed"])
    harness = RunHarness(
        operation_smoke_coordinator=smoke,
        tool_context=_context(),
    )

    first = harness.run(RESTScopeRunRequest())
    second = harness.run(RESTScopeRunRequest())

    assert [(item.round_number, item.attempt_number) for item in first.attempts] == [
        (1, 1),
        (1, 1),
    ]
    assert [(item.round_number, item.attempt_number) for item in second.attempts] == [
        (1, 1),
        (1, 1),
    ]
