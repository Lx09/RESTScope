"""Reusable test support for operation smoke stub scenarios; this module is not production runtime code."""

from __future__ import annotations

from typing import Any


class PassingOperationSmokeCoordinator:
    """Test-only Smoke Coordinator that performs no model or network calls."""

    def __init__(self, *, tracing_runtime: Any | None = None) -> None:
        self.tracing_runtime = tracing_runtime
        self.requests = []

    def run(self, context, request):
        from restscope.operation_smoke import OperationSmokeResult

        del context
        self.requests.append(request)
        result = OperationSmokeResult(
            status="passed",
            operation_key=request.operation_key,
            success_rate=1.0,
            required_success_rate=request.success_rate_threshold,
            active_config_revision=1,
            stop_reason="success_rate_reached",
            reason="The test double represents a successful complete Batch.",
        )
        if self.tracing_runtime is None:
            return result
        with self.tracing_runtime.span(
            "OperationSmokeCoordinator.run",
            kind="CHAIN",
            input_value=request,
        ) as span:
            span.set_output(result)
            return result
