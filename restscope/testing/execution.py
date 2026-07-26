"""Generate and execute bounded batches against the App-bound target."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import secrets
import time
from typing import Any, Mapping
from uuid import uuid4

from restscope.capabilities.tool_context import ToolContext
from restscope.http_transport import (
    PreparedTargetRequest,
    TargetHTTPTimeout,
    TargetHTTPTransport,
    TargetHTTPTransportError,
    TargetResponseOperationContext,
)
from restscope.observability import TracingRuntime

from .catalog import GeneratorConfigCatalog
from .failure_reporting import (
    MAX_FAILURE_RESPONSE_BYTES,
    FailureCaseEvidence,
    build_batch_failure_report,
)
from .generation import generate_test_case
from .models import (
    GeneratedTestCase,
    OperationExecutionReport,
    PreparedRequestSummary,
    PreparedTestRequest,
    BehaviorMonitorWarningSummary,
    ResponseSummary,
    TestCaseExecutionReport,
    TransportErrorSummary,
)
from .ports import ReferenceValueProvider
from .serialization import serialize_test_case


BEHAVIOR_MONITOR_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SmokeCaseExecutionEvidence:
    """Private response evidence retained only for one Smoke diagnosis."""

    case_id: str
    response_body: bytes | None = None
    response_body_truncated: bool = False
    response_encoding: str | None = None
    behavior_monitor: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SmokeExecutionOutcome:
    """Public report plus private, App-lifetime-only diagnosis evidence."""

    report: OperationExecutionReport
    case_evidence: tuple[SmokeCaseExecutionEvidence, ...]


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
        reference_values: ReferenceValueProvider | None = None,
    ) -> None:
        self.config_catalog = config_catalog
        self.transport = transport or TargetHTTPTransport()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.reference_values = reference_values

    def run_operation(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
    ) -> OperationExecutionReport:
        return self._run_operation_traced(
            context,
            operation_key=operation_key,
            case_count=case_count,
            seed=seed,
        ).report

    def run_operation_for_smoke(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
    ) -> SmokeExecutionOutcome:
        """Execute once while retaining bounded evidence outside the report."""

        return self._run_operation_traced(
            context,
            operation_key=operation_key,
            case_count=case_count,
            seed=seed,
        )

    def _run_operation_traced(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int | None,
    ) -> SmokeExecutionOutcome:
        with self.tracing_runtime.span(
            "OperationTestingService.run_operation",
            kind="CHAIN",
            input_value={
                "operation_key": operation_key,
                "case_count": case_count,
                "seed": seed,
            },
            attributes={
                "restscope.operation.key": operation_key,
                "restscope.test.case_count": case_count,
            },
        ) as span:
            outcome = self._run_operation(
                context,
                operation_key=operation_key,
                case_count=case_count,
                seed=seed,
            )
            report = outcome.report
            span.set_output(
                {
                    "run_id": report.run_id,
                    "status": report.status,
                    "config_revision": report.config_revision,
                    "status_code_counts": report.status_code_counts,
                    "error_count": report.error_count,
                    "observed_2xx": report.observed_2xx,
                    "response_validation": report.response_validation,
                    "behavior_monitor_warning_count": (
                        report.behavior_monitor_warning_count
                    ),
                    "failure_message_count": len(
                        report.failure_report.unique_failure_messages
                    ),
                }
            )
            span.set_attribute("restscope.test.run_id", report.run_id)
            span.set_attribute(
                "restscope.generator.config_revision",
                report.config_revision,
            )
            span.set_attribute("restscope.test.status", report.status)
            span.set_attribute(
                "restscope.test.error_count",
                report.error_count,
            )
            span.set_attribute(
                "restscope.test.observed_2xx",
                report.observed_2xx,
            )
            return outcome

    def _run_operation(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
    ) -> SmokeExecutionOutcome:
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
                reference_values=self.reference_values,
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
        failure_evidence: list[FailureCaseEvidence] = []
        smoke_evidence: list[SmokeCaseExecutionEvidence] = []
        for case_index, (generated, request, target_request) in enumerate(
            prepared
        ):
            case_report, case_failure_evidence, case_smoke_evidence = (
                self._execute_case(
                    context,
                    run_id=run_id,
                    case_index=case_index,
                    generated=generated,
                    request=request,
                    target_request=target_request,
                )
            )
            reports.append(case_report)
            failure_evidence.append(case_failure_evidence)
            smoke_evidence.append(case_smoke_evidence)

        status_counts = Counter(
            str(case.response.status_code)
            for case in reports
            if case.response is not None
        )
        error_count = sum(case.transport_error is not None for case in reports)
        warning_count = sum(
            len(case.behavior_monitor_warnings) for case in reports
        )
        response_validation = _response_validation(reports)
        status = (
            "completed"
            if error_count == 0
            else "errored"
            if error_count == len(reports)
            else "partial"
        )
        report = OperationExecutionReport(
            run_id=run_id,
            operation_key=operation_key,
            seed=run_seed,
            config_revision=config.revision,
            status=status,
            response_validation=response_validation,
            cases=reports,
            status_code_counts=dict(status_counts),
            error_count=error_count,
            observed_2xx=any(
                case.response is not None and 200 <= case.response.status_code < 300
                for case in reports
            ),
            behavior_monitor_warning_count=warning_count,
            failure_report=build_batch_failure_report(failure_evidence),
        )
        return SmokeExecutionOutcome(
            report=OperationExecutionReport.model_validate(
                self.tracing_runtime.redactor.redact(report)
            ),
            case_evidence=tuple(smoke_evidence),
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
    ) -> tuple[
        TestCaseExecutionReport,
        FailureCaseEvidence,
        SmokeCaseExecutionEvidence,
    ]:
        case_id = f"{run_id}_case_{case_index + 1}"
        request_summary = _request_summary(
            request,
            target_request=target_request,
        )
        started = time.perf_counter()
        response_summary: ResponseSummary | None = None
        error_summary: TransportErrorSummary | None = None
        monitor_warnings: list[BehaviorMonitorWarningSummary] = []
        response_validation = "not_evaluated"
        failure_evidence = FailureCaseEvidence(case_id=case_id)
        smoke_evidence = SmokeCaseExecutionEvidence(case_id=case_id)
        with self.tracing_runtime.span(
            "RESTScopeTestCase.execute",
            kind="TOOL",
            input_value=request_summary,
            attributes={
                "restscope.operation.key": generated.operation_key,
                "restscope.test.run_id": run_id,
                "restscope.test.case_id": case_id,
                "restscope.test.case_index": case_index,
            },
        ) as span:
            try:
                response = self.transport.request_prepared(
                    target_request,
                    timeout_seconds=30,
                    request_kwargs=(
                        {"content": request.content}
                        if request.content is not None
                        else {}
                    ),
                    response_body_limit=(
                        BEHAVIOR_MONITOR_RESPONSE_BYTES
                        if self.transport.has_response_processor
                        else None
                    ),
                    failure_response_body_limit=MAX_FAILURE_RESPONSE_BYTES,
                    truncate_response_body=True,
                    buffer_success_body_only=True,
                    processor_context=TargetResponseOperationContext(
                        ir=context.ir,
                        operation_key=generated.operation_key,
                        operation_method=request.method,
                        operation_path=self.config_catalog.require_operation(
                            generated.operation_key
                        ).snapshot.path,
                    ),
                )
                elapsed = (time.perf_counter() - started) * 1000
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    or None
                )
                raw_length = response.headers.get("content-length")
                response_summary = ResponseSummary(
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    media_type=media_type,
                    content_length=(
                        int(raw_length)
                        if raw_length and raw_length.isdigit()
                        else None
                    ),
                    latency_ms=elapsed,
                )
                if response.processor_result is not None:
                    response_validation = (
                        response.processor_result.response_validation
                    )
                    monitor_warnings = [
                        BehaviorMonitorWarningSummary(
                            code=warning.code,
                            message=warning.message,
                            issues=list(warning.issues),
                        )
                        for warning in response.processor_result.warnings
                    ]
                failure_evidence = FailureCaseEvidence(
                    case_id=case_id,
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    media_type=media_type,
                    body=response.body,
                    body_truncated=response.body_truncated,
                    encoding=response.encoding,
                )
                smoke_evidence = SmokeCaseExecutionEvidence(
                    case_id=case_id,
                    response_body=(
                        response.body
                        if not 200 <= response.status_code < 300
                        else None
                    ),
                    response_body_truncated=response.body_truncated,
                    response_encoding=response.encoding,
                    behavior_monitor=(
                        response.processor_result.details
                        if response.processor_result is not None
                        else None
                    ),
                )
                span.set_output(response_summary)
            except TargetHTTPTimeout:
                error_summary = TransportErrorSummary(
                    code="request_timeout",
                    message="HTTP request timed out",
                )
                failure_evidence = FailureCaseEvidence(
                    case_id=case_id,
                    transport_error_code=error_summary.code,
                    transport_error_message=error_summary.message,
                )
                span.mark_error(error_summary.message)
            except TargetHTTPTransportError as exc:
                error_summary = TransportErrorSummary(
                    code=exc.code,
                    message=str(exc),
                )
                failure_evidence = FailureCaseEvidence(
                    case_id=case_id,
                    transport_error_code=error_summary.code,
                    transport_error_message=error_summary.message,
                )
                span.mark_error(error_summary.message)

        return (
            TestCaseExecutionReport(
                case_id=case_id,
                generated_test_case=generated,
                request=request_summary,
                response=response_summary,
                transport_error=error_summary,
                behavior_monitor_warnings=monitor_warnings,
                response_validation=response_validation,
            ),
            failure_evidence,
            smoke_evidence,
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


def _response_validation(
    cases: list[TestCaseExecutionReport],
) -> str:
    statuses = [
        case.response_validation
        for case in cases
        if case.response is not None
    ]
    if not statuses or all(status == "not_evaluated" for status in statuses):
        return "not_evaluated"
    if any(status == "partial" for status in statuses):
        return "partial"
    return "evaluated"


def _target_query_items(
    request: PreparedTestRequest,
) -> list[tuple[str, str] | tuple[str, str, bool]]:
    allow_reserved = set(request.query_allow_reserved_indices)
    return [
        (name, value, True) if index in allow_reserved else (name, value)
        for index, (name, value) in enumerate(request.query_items)
    ]
