"""Generate and execute bounded batches against the App-bound target."""

from __future__ import annotations

from collections import Counter
import secrets
import time
from uuid import uuid4

from restscope.capabilities.tool_context import ToolContext
from restscope.http_transport import (
    PreparedTargetRequest,
    TargetHTTPTimeout,
    TargetHTTPTransport,
    TargetHTTPTransportError,
)
from restscope.observability import TracingRuntime

from .catalog import GeneratorConfigCatalog
from .generation import generate_test_case
from .models import (
    GeneratedTestCase,
    OperationExecutionReport,
    PreparedRequestSummary,
    PreparedTestRequest,
    ResponseSummary,
    TestCaseExecutionReport,
    TransportErrorSummary,
)
from .serialization import serialize_test_case


class TestingExecutionError(RuntimeError):
    """Stable preflight error raised before any target request is sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OperationTestingService:
    """Preflight and sequentially execute one configured operation batch."""

    def __init__(
        self,
        *,
        config_catalog: GeneratorConfigCatalog,
        transport: TargetHTTPTransport | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.config_catalog = config_catalog
        self.transport = transport or TargetHTTPTransport()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run_operation(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
    ) -> OperationExecutionReport:
        if not 1 <= case_count <= 20:
            raise TestingExecutionError(
                "invalid_case_count",
                "case_count must be between 1 and 20",
            )
        config = self.config_catalog.require_operation(operation_key)
        operation = config.snapshot
        run_seed = seed if seed is not None else secrets.randbits(63)
        prepared: list[
            tuple[GeneratedTestCase, PreparedTestRequest, PreparedTargetRequest]
        ] = []
        for case_index in range(case_count):
            generated = generate_test_case(
                operation,
                config,
                run_seed=run_seed,
                case_index=case_index,
            )
            request = serialize_test_case(operation, generated)
            target_request = self.transport.prepare(
                method=request.method,
                base_url=context.base_url,
                path=request.path,
                query_items=_target_query_items(request),
                context_headers=context.headers,
                request_headers=request.headers,
                override_context_headers=True,
                allowed_sensitive_request_headers={"cookie"},
            )
            prepared.append((generated, request, target_request))

        run_id = f"test_run_{uuid4().hex}"
        reports: list[TestCaseExecutionReport] = []
        for case_index, (generated, request, target_request) in enumerate(prepared):
            reports.append(
                self._execute_case(
                    context,
                    run_id=run_id,
                    case_index=case_index,
                    generated=generated,
                    request=request,
                    target_request=target_request,
                )
            )

        status_counts = Counter(
            str(case.response.status_code)
            for case in reports
            if case.response is not None
        )
        error_count = sum(case.transport_error is not None for case in reports)
        status = "completed" if error_count == 0 else "errored" if error_count == len(reports) else "partial"
        report = OperationExecutionReport(
            run_id=run_id,
            operation_key=operation_key,
            seed=run_seed,
            config_revision=config.revision,
            status=status,
            cases=reports,
            status_code_counts=dict(status_counts),
            error_count=error_count,
            observed_2xx=any(
                case.response is not None and 200 <= case.response.status_code < 300
                for case in reports
            ),
        )
        return OperationExecutionReport.model_validate(
            self.tracing_runtime.redactor.redact(report)
        )

    def _execute_case(
        self,
        context: ToolContext,
        *,
        run_id: str,
        case_index: int,
        generated: GeneratedTestCase,
        request: PreparedTestRequest,
        target_request: PreparedTargetRequest,
    ) -> TestCaseExecutionReport:
        case_id = f"{run_id}_case_{case_index + 1}"
        request_summary = _request_summary(
            request,
            target_request=target_request,
        )
        started = time.perf_counter()
        response_summary: ResponseSummary | None = None
        error_summary: TransportErrorSummary | None = None
        with self.tracing_runtime.span(
            "RESTScopeTestCase.execute",
            kind="TOOL",
            input_value=request_summary,
            attributes={
                "restscope.operation.key": generated.operation_key,
                "restscope.test.case_index": case_index,
            },
        ) as span:
            try:
                with self.transport.stream_prepared(
                    target_request,
                    timeout_seconds=30,
                    request_kwargs={"content": request.content} if request.content is not None else {},
                ) as response:
                    elapsed = (time.perf_counter() - started) * 1000
                    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or None
                    raw_length = response.headers.get("content-length")
                    response_summary = ResponseSummary(
                        status_code=response.status_code,
                        reason_phrase=response.reason_phrase,
                        media_type=media_type,
                        content_length=int(raw_length) if raw_length and raw_length.isdigit() else None,
                        latency_ms=elapsed,
                    )
                    span.set_output(response_summary)
            except TargetHTTPTimeout:
                error_summary = TransportErrorSummary(
                    code="request_timeout",
                    message="HTTP request timed out",
                )
                span.mark_error(error_summary.message)
            except TargetHTTPTransportError as exc:
                error_summary = TransportErrorSummary(code=exc.code, message=str(exc))
                span.mark_error(error_summary.message)

        return TestCaseExecutionReport(
            case_id=case_id,
            generated_test_case=generated,
            request=request_summary,
            response=response_summary,
            transport_error=error_summary,
        )


def _request_summary(
    request: PreparedTestRequest,
    *,
    target_request: PreparedTargetRequest,
) -> PreparedRequestSummary:
    return PreparedRequestSummary(
        method=request.method,
        path=request.path,
        query_items=list(request.query_items),
        headers=dict(target_request.headers),
        body_size_bytes=len(request.content or b""),
    )


def _target_query_items(
    request: PreparedTestRequest,
) -> list[tuple[str, str] | tuple[str, str, bool]]:
    allow_reserved = set(request.query_allow_reserved_indices)
    return [
        (name, value, True) if index in allow_reserved else (name, value)
        for index, (name, value) in enumerate(request.query_items)
    ]
