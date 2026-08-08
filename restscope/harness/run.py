"""Run every current OpenAPI operation through the deterministic Harness.

The App creates one :class:`RunHarness` for each public ``run`` call. Its input
is a bounded run request and its output is a chronological report. Operation
queues, retry counters, and partial report state live only inside that call;
they are never persisted or exposed as an Agent plan.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from restscope.observability import TracingRuntime
from restscope.openapi_parser.ir import OperationIR
from restscope.operation_smoke import (
    OperationSmokeCoordinator,
    OperationSmokeRequest,
    OperationSmokeResult,
)
from restscope.operations import OperationReference
from restscope.tools import ToolContext


RunStatus = Literal["passed", "failed", "errored"]
AttemptDisposition = Literal[
    "satisfied",
    "retrying",
    "unsupported",
    "failed",
    "errored",
]
OperationFailureKind = Literal[
    "failure_resolution_limit_exceeded",
    "unsupported_operation",
    "operation_error",
    "provider_unavailable",
]
StopReason = Literal[
    "completed",
    "completed_with_failures",
    "technical_error",
]


class RESTScopeRunRequest(BaseModel):
    """Configure one bounded, ephemeral Harness run.

    ``max_operation_attempts`` applies independently to each operation. Metadata
    is returned to tracing only where an explicitly supported field, currently
    ``task_id``, is selected.
    """

    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)
    max_operation_attempts: int = Field(default=3, ge=1, le=10)


class OperationAttempt(BaseModel):
    """Record one chronological Operation Smoke invocation in the run report."""

    model_config = ConfigDict(extra="forbid")

    operation: OperationReference
    round_number: int = Field(ge=1)
    attempt_number: int = Field(ge=1)
    smoke_result: OperationSmokeResult
    disposition: AttemptDisposition
    failure_kind: OperationFailureKind | None = None


class RESTScopeRunReport(BaseModel):
    """Return the complete observable result of one Harness run."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(default_factory=lambda: f"restscope_run_{uuid4().hex}")
    random_seed: int = Field(ge=0)
    status: RunStatus
    stop_reason: StopReason
    operations: list[OperationReference] = Field(default_factory=list)
    attempts: list[OperationAttempt] = Field(default_factory=list)
    satisfied_operations: list[OperationReference] = Field(default_factory=list)
    unattempted_operations: list[OperationReference] = Field(default_factory=list)
    rounds: int = 0
    attempt_count: int = 0
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _RunState:
    """Keep mutable scheduler facts private to one ``RunHarness.run`` call."""

    operations: list[OperationReference] = field(default_factory=list)
    ready_queue: deque[OperationReference] = field(default_factory=deque)
    retry_queue: deque[OperationReference] = field(default_factory=deque)
    satisfied: list[OperationReference] = field(default_factory=list)
    attempts: list[OperationAttempt] = field(default_factory=list)
    attempt_counts: dict[str, int] = field(default_factory=dict)
    current_round: int = 0
    rounds: int = 0
    error: dict[str, Any] | None = None


class RunHarness:
    """Schedule current operations through a small, synchronous Interface.

    Args:
        operation_smoke_coordinator: Executes one complete operation-level Smoke
            workflow whenever the Harness selects an operation.
        tool_context: Current App-bound OpenAPI representation and target state.
        random_seed: Seed copied into the final report for reproducibility.
        tracing_runtime: Optional trace sink for the run and each attempt.

    The Harness owns deterministic ordering and retries only. All LLM-owned
    investigation remains inside the injected Operation Smoke Coordinator.
    """

    def __init__(
        self,
        *,
        operation_smoke_coordinator: OperationSmokeCoordinator,
        tool_context: ToolContext,
        random_seed: int = 0,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Bind dependencies without discovering or executing an operation."""
        if operation_smoke_coordinator is None:
            raise ValueError("Run Harness requires an OperationSmokeCoordinator")
        self.operation_smoke_coordinator = operation_smoke_coordinator
        self.tool_context = tool_context
        self.random_seed = random_seed
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        """Execute all discovered operations and return one final report.

        Operation-local failures may enter a later round up to the request's
        limit. A programming/infrastructure exception or shared model-provider
        outage stops the run because continuing cannot safely improve it.
        """
        task_id = request.metadata.get("task_id")
        attributes = {"restscope.task_id": task_id} if task_id else {}
        with self.tracing_runtime.span(
            "RunHarness.run",
            kind="CHAIN",
            input_value=request,
            attributes=attributes,
        ) as span:
            state = _RunState()
            self._discover_operations(state)
            while state.error is None and state.ready_queue:
                self._run_next_operation(state, request=request)
                if state.error is not None:
                    break
                if not state.ready_queue and state.retry_queue:
                    state.ready_queue = state.retry_queue
                    state.retry_queue = deque()
                    state.current_round += 1
                    state.rounds += 1

            report = self._build_report(state)
            span.set_output(_run_trace_summary(report))
            span.set_attribute("restscope.run.status", report.status)
            return report

    def _discover_operations(self, state: _RunState) -> None:
        """Populate a stable shallow-path-first queue from the current IR."""
        try:
            indexed = list(enumerate(self.tool_context.ir.operations.values()))
            ordered_ir = [
                operation
                for _, operation in sorted(
                    indexed,
                    key=lambda item: (_path_depth(item[1].path), item[0]),
                )
            ]
            state.operations = [
                _operation_reference(operation) for operation in ordered_ir
            ]
            state.ready_queue = deque(state.operations)
            state.current_round = 1
            state.rounds = 1
        except Exception as exc:
            state.error = _technical_error(exc, stage="discover_operations")

    def _run_next_operation(
        self,
        state: _RunState,
        *,
        request: RESTScopeRunRequest,
    ) -> None:
        """Execute and classify the next ready operation without recursion."""
        operation = state.ready_queue.popleft()
        identity_key = _identity_key(operation)
        attempt_number = state.attempt_counts.get(identity_key, 0) + 1
        state.attempt_counts[identity_key] = attempt_number
        task_id = request.metadata.get("task_id")
        attributes: dict[str, Any] = {
            "restscope.operation.key": _operation_key(operation),
            "restscope.operation.method": operation.method,
            "restscope.operation.path": operation.path,
            "restscope.operation.round": state.current_round,
            "restscope.operation.attempt": attempt_number,
        }
        if task_id:
            attributes["restscope.task_id"] = task_id

        with self.tracing_runtime.span(
            "RunHarness.operation_attempt",
            kind="CHAIN",
            input_value={
                "operation_key": _operation_key(operation),
                "round_number": state.current_round,
                "attempt_number": attempt_number,
            },
            attributes=attributes,
        ) as span:
            try:
                smoke_result = self.operation_smoke_coordinator.run(
                    self.tool_context,
                    OperationSmokeRequest(operation_key=_operation_key(operation)),
                )
            except Exception as exc:
                state.error = _technical_error(
                    exc,
                    stage="run_next_operation",
                    operation=operation,
                )
                span.set_output({"disposition": "global_error", "error": state.error})
                span.set_attribute("restscope.operation.disposition", "global_error")
                span.mark_error(str(state.error["message"]))
                return

            disposition = _attempt_disposition(
                smoke_result,
                attempt_number=attempt_number,
                max_attempts=request.max_operation_attempts,
            )
            if disposition == "satisfied":
                state.satisfied.append(operation)
            elif disposition == "retrying":
                state.retry_queue.append(operation)

            attempt = OperationAttempt(
                operation=operation,
                round_number=state.current_round,
                attempt_number=attempt_number,
                smoke_result=smoke_result,
                disposition=disposition,
                failure_kind=smoke_result.failure_kind,
            )
            state.attempts.append(attempt)

            if smoke_result.failure_kind == "provider_unavailable":
                error = smoke_result.error or {}
                state.error = {
                    "stage": "run_next_operation",
                    "type": "ProviderUnavailableError",
                    "message": error.get(
                        "message",
                        "provider_unavailable: Model provider is unavailable.",
                    ),
                    "operation": operation.model_dump(mode="json"),
                }
                span.set_output({"disposition": "global_error", "error": state.error})
                span.set_attribute("restscope.operation.disposition", "global_error")
                span.mark_error(str(state.error["message"]))
            else:
                _finish_attempt_span(span, attempt)

    def _build_report(self, state: _RunState) -> RESTScopeRunReport:
        """Derive final status and operation coverage from private run state."""
        attempted_ids = {attempt.operation.identity() for attempt in state.attempts}
        unattempted = [
            operation
            for operation in state.operations
            if operation.identity() not in attempted_ids
        ]
        if state.error is not None:
            status: RunStatus = "errored"
            stop_reason: StopReason = "technical_error"
        elif len(state.satisfied) == len(state.operations):
            status = "passed"
            stop_reason = "completed"
        else:
            status = "failed"
            stop_reason = "completed_with_failures"
        return RESTScopeRunReport(
            random_seed=self.random_seed,
            status=status,
            stop_reason=stop_reason,
            operations=state.operations,
            attempts=state.attempts,
            satisfied_operations=state.satisfied,
            unattempted_operations=unattempted,
            rounds=state.rounds,
            attempt_count=len(state.attempts),
            error=state.error,
            metadata={
                "harness": "run",
                "discovered_operation_count": len(state.operations),
                "satisfied_operation_count": len(state.satisfied),
            },
        )


def _attempt_disposition(
    result: OperationSmokeResult,
    *,
    attempt_number: int,
    max_attempts: int,
) -> AttemptDisposition:
    """Map a bounded Smoke result to the Harness's next deterministic action."""
    if result.status == "passed":
        return "satisfied"
    if result.status == "unsupported":
        return "unsupported"
    if result.failure_kind == "provider_unavailable":
        return "errored"
    if attempt_number < max_attempts:
        return "retrying"
    return "errored" if result.status == "errored" else "failed"


def _finish_attempt_span(span: Any, attempt: OperationAttempt) -> None:
    """Record one bounded attempt summary without exposing response bodies."""
    span.set_output(
        {
            "disposition": attempt.disposition,
            "failure_kind": attempt.failure_kind,
            "smoke_status": attempt.smoke_result.status,
            "batch_count": len(attempt.smoke_result.batch_run_ids),
        }
    )
    span.set_attribute("restscope.operation.disposition", attempt.disposition)
    span.set_attribute("restscope.operation.smoke_status", attempt.smoke_result.status)
    if attempt.failure_kind is not None:
        span.set_attribute("restscope.operation.failure_kind", attempt.failure_kind)


def _technical_error(
    exc: Exception,
    *,
    stage: str,
    operation: OperationReference | None = None,
) -> dict[str, Any]:
    """Convert an unexpected Harness failure into a bounded report value."""
    payload: dict[str, Any] = {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
    }
    if operation is not None:
        payload["operation"] = operation.model_dump(mode="json")
    return payload


def _run_trace_summary(report: RESTScopeRunReport) -> dict[str, Any]:
    """Return low-cardinality run facts for tracing and human diagnosis."""
    disposition_counts: dict[str, int] = {}
    failure_kind_counts: dict[str, int] = {}
    for attempt in report.attempts:
        disposition_counts[attempt.disposition] = (
            disposition_counts.get(attempt.disposition, 0) + 1
        )
        if attempt.failure_kind is not None:
            failure_kind_counts[attempt.failure_kind] = (
                failure_kind_counts.get(attempt.failure_kind, 0) + 1
            )
    return {
        "report_id": report.report_id,
        "status": report.status,
        "stop_reason": report.stop_reason,
        "operation_count": len(report.operations),
        "attempt_count": report.attempt_count,
        "rounds": report.rounds,
        "satisfied_operation_count": len(report.satisfied_operations),
        "unattempted_operation_count": len(report.unattempted_operations),
        "disposition_counts": disposition_counts,
        "failure_kind_counts": failure_kind_counts,
        "error": report.error,
    }


def _path_depth(path: str) -> int:
    """Count non-empty path segments for stable shallow-route-first ordering."""
    return sum(1 for segment in path.split("/") if segment)


def _operation_reference(operation: OperationIR) -> OperationReference:
    """Project one current IR operation into the public report identity."""
    return OperationReference(
        method=operation.method,
        path=operation.path,
        operation_id=operation.operation_id,
    )


def _identity_key(operation: OperationReference) -> str:
    """Return an unambiguous in-memory key for one operation's retry count."""
    return (
        f"{operation.method}\0{operation.path}\0"
        f"{operation.operation_id or ''}"
    )


def _operation_key(operation: OperationReference) -> str:
    """Return the exact METHOD/path key accepted by Operation Smoke."""
    return f"{operation.method} {operation.path}"
