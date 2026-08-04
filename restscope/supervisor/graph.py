"""Schedule Operation Smoke work in an App-lifetime FIFO graph.

The graph discovers operations from the current OpenAPI IR, receives bounded
Smoke results, and returns a complete run report. It retries operation-local
failures by round but stops immediately when shared infrastructure such as the
single model provider is unavailable; no queue state is persisted.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from restscope.capabilities import ToolContext
from restscope.openapi_parser.ir import OperationIR
from restscope.operations import OperationReference
from restscope.observability import TracingRuntime

from ..operation_smoke import (
    OperationSmokeCoordinator,
    OperationSmokeRequest,
    OperationSmokeResult,
)
from .schemas import (
    AttemptDisposition,
    OperationAttempt,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    RunStatus,
    StopReason,
)


class RESTScopeMainState(TypedDict, total=False):
    """Serializable scheduler state; ToolContext remains runtime-only."""

    request: dict[str, Any]
    operations: list[dict[str, Any]]
    ready_queue: list[dict[str, Any]]
    retry_queue: list[dict[str, Any]]
    satisfied: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    attempt_counts: dict[str, int]
    current_round: int
    rounds: int
    status: RunStatus
    stop_reason: StopReason
    last_error: dict[str, Any]
    final_report: dict[str, Any]


class RESTScopeMainGraph:
    """Discover operations and schedule one Smoke attempt per operation per round.

    The graph is a runtime queue, not a persisted test plan.  Shallow routes run
    first because they often discover identifiers consumed by deeper routes.
    Failed-but-retryable operations move to the next round; successful and
    unsupported operations leave the queue; a technical error stops the entire
    run because shared infrastructure may be unavailable.
    """

    def __init__(
        self,
        *,
        operation_smoke_coordinator: OperationSmokeCoordinator,
        tool_context: ToolContext,
        random_seed: int = 0,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        if operation_smoke_coordinator is None:
            raise ValueError("Supervisor requires an OperationSmokeCoordinator")
        self.operation_smoke_coordinator = operation_smoke_coordinator
        self.tool_context = tool_context
        self.random_seed = random_seed
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        """Execute the ephemeral scheduling graph and return its final report."""
        task_id = request.metadata.get("task_id")
        attributes = {"restscope.task_id": task_id} if task_id else {}
        with self.tracing_runtime.span(
            "RESTScopeMainGraph.run",
            kind="CHAIN",
            input_value=request,
            attributes=attributes,
        ) as span:
            final_state = self._build_graph(request=request).invoke(
                {
                    "request": request.model_dump(mode="json"),
                    "operations": [],
                    "ready_queue": [],
                    "retry_queue": [],
                    "satisfied": [],
                    "attempts": [],
                    "attempt_counts": {},
                    "current_round": 0,
                    "rounds": 0,
                },
                config={"recursion_limit": 10_000},
            )
            report = RESTScopeRunReport.model_validate(
                final_state["final_report"]
            )
            span.set_output(_graph_run_trace_summary(report))
            span.set_attribute("restscope.run.status", report.status)
            return report

    def _build_graph(self, *, request: RESTScopeRunRequest) -> Any:
        """Create the four-node LangGraph state machine for this invocation."""
        # LangGraph accepts standard TypedDict state at runtime, but its
        # published generic bounds are not understood consistently by static
        # IDE inspection. Keep that escape at this library boundary; every
        # RESTScope node remains fully typed.
        graph: Any = StateGraph(cast(Any, RESTScopeMainState))
        graph.add_node("discover_operations", self._discover_operations)
        graph.add_node("run_next_operation", self._run_next_operation(request))
        graph.add_node("advance_round", self._advance_round)
        graph.add_node("finalize_report", self._finalize_report)

        # Conditional edges make queue state, rather than a precomputed plan,
        # decide whether to run another operation, advance a round, or finish.
        graph.add_edge(START, "discover_operations")
        graph.add_conditional_edges(
            "discover_operations",
            self._route_after_discovery,
            {"finalize": "finalize_report", "run": "run_next_operation"},
        )
        graph.add_conditional_edges(
            "run_next_operation",
            self._route_after_attempt,
            {
                "finalize": "finalize_report",
                "run": "run_next_operation",
                "advance": "advance_round",
            },
        )
        graph.add_conditional_edges(
            "advance_round",
            self._route_after_advance,
            {"finalize": "finalize_report", "run": "run_next_operation"},
        )
        graph.add_edge("finalize_report", END)
        return graph.compile()

    def _discover_operations(
        self,
        state: RESTScopeMainState,
    ) -> RESTScopeMainState:
        """Snapshot current IR operations into a shallow-route-first FIFO queue."""
        del state
        try:
            indexed = list(enumerate(self.tool_context.ir.operations.values()))
            ordered_ir = [
                operation
                for _, operation in sorted(
                    indexed,
                    key=lambda item: (_path_depth(item[1].path), item[0]),
                )
            ]
            operations = [
                _operation_reference(operation) for operation in ordered_ir
            ]
            serialized = [
                operation.model_dump(mode="json") for operation in operations
            ]
            return {
                "operations": serialized,
                "ready_queue": serialized,
                "retry_queue": [],
                "current_round": 1,
                "rounds": 1,
            }
        except Exception as exc:
            return self._technical_error(exc, stage="discover_operations")

    def _run_next_operation(self, request: RESTScopeRunRequest):
        """Return a graph node that consumes one ready operation."""
        def execute(state: RESTScopeMainState) -> RESTScopeMainState:
            # Copy list/dict values before mutation because LangGraph state may
            # retain the prior objects for tracing and state reduction.
            ready = list(state.get("ready_queue", []))
            if not ready:
                return {}
            operation = OperationReference.model_validate(ready.pop(0))
            counts = dict(state.get("attempt_counts", {}))
            identity_key = _identity_key(operation)
            attempt_number = counts.get(identity_key, 0) + 1
            counts[identity_key] = attempt_number

            try:
                smoke_result = self.operation_smoke_coordinator.run(
                    self.tool_context,
                    OperationSmokeRequest(
                        operation_key=_operation_key(operation),
                    ),
                )
            except Exception as exc:
                return {
                    "ready_queue": ready,
                    "attempt_counts": counts,
                    **self._technical_error(
                        exc,
                        stage="run_next_operation",
                        operation=operation,
                    ),
                }

            updates: RESTScopeMainState = {}
            retry_queue = list(state.get("retry_queue", []))
            # Disposition is the only scheduler decision derived from the rich
            # Smoke result.  The full result is still retained in the attempt
            # report for later diagnosis.
            disposition = _attempt_disposition(
                smoke_result,
                attempt_number=attempt_number,
                max_attempts=request.max_operation_attempts,
            )
            if disposition == "satisfied":
                updates["satisfied"] = [
                    *state.get("satisfied", []),
                    operation.model_dump(mode="json"),
                ]
            elif disposition == "retrying":
                retry_queue.append(operation.model_dump(mode="json"))

            attempt = OperationAttempt(
                operation=operation,
                round_number=int(state.get("current_round", 1)),
                attempt_number=attempt_number,
                smoke_result=smoke_result,
                disposition=disposition,
                failure_kind=smoke_result.failure_kind,
            )
            updates.update(
                {
                    "ready_queue": ready,
                    "retry_queue": retry_queue,
                    "attempt_counts": counts,
                    "attempts": [
                        *state.get("attempts", []),
                        attempt.model_dump(mode="json"),
                    ],
                }
            )
            if smoke_result.failure_kind == "provider_unavailable":
                # The current attempt is evidence and must reach the report,
                # but a single-provider outage cannot improve by retrying an
                # operation or continuing with a later operation in this run.
                error = smoke_result.error or {}
                updates.update(
                    {
                        "status": "errored",
                        "stop_reason": "technical_error",
                        "last_error": {
                            "stage": "run_next_operation",
                            "type": "ProviderUnavailableError",
                            "message": error.get(
                                "message",
                                "provider_unavailable: Model provider is unavailable.",
                            ),
                            "operation": operation.model_dump(mode="json"),
                        },
                    }
                )
            return updates

        def node(state: RESTScopeMainState) -> RESTScopeMainState:
            ready = list(state.get("ready_queue", []))
            if not ready:
                return execute(state)
            operation = OperationReference.model_validate(ready[0])
            identity_key = _identity_key(operation)
            attempt_number = (
                dict(state.get("attempt_counts", {})).get(identity_key, 0) + 1
            )
            round_number = int(state.get("current_round", 1))
            task_id = request.metadata.get("task_id")
            attributes: dict[str, Any] = {
                "restscope.operation.key": _operation_key(operation),
                "restscope.operation.method": operation.method,
                "restscope.operation.path": operation.path,
                "restscope.operation.round": round_number,
                "restscope.operation.attempt": attempt_number,
            }
            if task_id:
                attributes["restscope.task_id"] = task_id
            with self.tracing_runtime.span(
                "RESTScopeMainGraph.operation_attempt",
                kind="CHAIN",
                input_value={
                    "operation_key": _operation_key(operation),
                    "round_number": round_number,
                    "attempt_number": attempt_number,
                },
                attributes=attributes,
            ) as span:
                updates = execute(state)
                if updates.get("last_error") is not None:
                    error = updates["last_error"]
                    span.set_output(
                        {"disposition": "global_error", "error": error}
                    )
                    span.set_attribute(
                        "restscope.operation.disposition",
                        "global_error",
                    )
                    span.mark_error(
                        str(error.get("message", "Operation attempt failed"))
                    )
                    return updates
                attempts = updates.get("attempts", [])
                if attempts:
                    attempt = OperationAttempt.model_validate(attempts[-1])
                    span.set_output(
                        {
                            "disposition": attempt.disposition,
                            "failure_kind": attempt.failure_kind,
                            "smoke_status": attempt.smoke_result.status,
                            "batch_count": len(
                                attempt.smoke_result.batch_run_ids
                            ),
                        }
                    )
                    span.set_attribute(
                        "restscope.operation.disposition",
                        attempt.disposition,
                    )
                    span.set_attribute(
                        "restscope.operation.smoke_status",
                        attempt.smoke_result.status,
                    )
                    if attempt.failure_kind is not None:
                        span.set_attribute(
                            "restscope.operation.failure_kind",
                            attempt.failure_kind,
                        )
                return updates

        return node

    def _advance_round(
        self,
        state: RESTScopeMainState,
    ) -> RESTScopeMainState:
        """Promote deferred operations to the next round or mark the run done."""

        retry = list(state.get("retry_queue", []))
        if retry:
            return {
                "ready_queue": retry,
                "retry_queue": [],
                "current_round": int(state.get("current_round", 1)) + 1,
                "rounds": int(state.get("rounds", 1)) + 1,
            }

        if len(state.get("satisfied", [])) == len(
            state.get("operations", [])
        ):
            return {"status": "passed", "stop_reason": "completed"}
        return {
            "status": "failed",
            "stop_reason": "completed_with_failures",
        }

    def _finalize_report(
        self,
        state: RESTScopeMainState,
    ) -> RESTScopeMainState:
        """
        Handle finalize report as part of the dynamic top-level operation scheduling
        loop.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        operations = [
            OperationReference.model_validate(item)
            for item in state.get("operations", [])
        ]
        attempts = [
            OperationAttempt.model_validate(item)
            for item in state.get("attempts", [])
        ]
        satisfied_ids = {
            OperationReference.model_validate(item).identity()
            for item in state.get("satisfied", [])
        }
        satisfied = [
            operation
            for operation in operations
            if operation.identity() in satisfied_ids
        ]
        attempted_ids = {
            attempt.operation.identity() for attempt in attempts
        }
        unattempted = [
            operation
            for operation in operations
            if operation.identity() not in attempted_ids
        ]
        error = state.get("last_error")
        status: RunStatus = (
            "errored"
            if error is not None
            else state["status"]
            if "status" in state
            else "failed"
        )
        stop_reason: StopReason = (
            "technical_error"
            if error is not None
            else state["stop_reason"]
            if "stop_reason" in state
            else "technical_error"
        )
        report = RESTScopeRunReport(
            report_id=f"restscope_run_{uuid4().hex}",
            random_seed=self.random_seed,
            status=status,
            stop_reason=stop_reason,
            operations=operations,
            attempts=attempts,
            satisfied_operations=satisfied,
            unattempted_operations=unattempted,
            rounds=int(state.get("rounds", 0)),
            attempt_count=len(attempts),
            error=error,
            metadata={
                "graph": "restscope_main_graph",
                "discovered_operation_count": len(operations),
                "satisfied_operation_count": len(satisfied),
            },
        )
        return {"final_report": report.model_dump(mode="json")}

    @staticmethod
    def _route_after_discovery(state: RESTScopeMainState) -> str:
        return "finalize" if state.get("last_error") else "run"

    @staticmethod
    def _route_after_attempt(state: RESTScopeMainState) -> str:
        if state.get("last_error"):
            return "finalize"
        return "run" if state.get("ready_queue") else "advance"

    @staticmethod
    def _route_after_advance(state: RESTScopeMainState) -> str:
        return (
            "finalize"
            if state.get("status") in {"passed", "failed", "errored"}
            else "run"
        )

    @staticmethod
    def _technical_error(
        exc: Exception,
        *,
        stage: str,
        operation: OperationReference | None = None,
    ) -> RESTScopeMainState:
        payload: dict[str, Any] = {
            "stage": stage,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        if operation is not None:
            payload["operation"] = operation.model_dump(mode="json")
        return {
            "status": "errored",
            "stop_reason": "technical_error",
            "last_error": payload,
        }


def _attempt_disposition(
    result: OperationSmokeResult,
    *,
    attempt_number: int,
    max_attempts: int,
) -> AttemptDisposition:
    if result.status == "passed":
        return "satisfied"
    if result.status == "unsupported":
        return "unsupported"
    if result.failure_kind == "provider_unavailable":
        return "errored"
    if attempt_number < max_attempts:
        return "retrying"
    return "errored" if result.status == "errored" else "failed"


def _graph_run_trace_summary(report: RESTScopeRunReport) -> dict[str, Any]:
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
    return sum(1 for segment in path.split("/") if segment)


def _operation_reference(operation: OperationIR) -> OperationReference:
    return OperationReference(
        method=operation.method,
        path=operation.path,
        operation_id=operation.operation_id,
    )


def _identity_key(operation: OperationReference) -> str:
    return (
        f"{operation.method}\0{operation.path}\0"
        f"{operation.operation_id or ''}"
    )


def _operation_key(operation: OperationReference) -> str:
    return f"{operation.method} {operation.path}"
