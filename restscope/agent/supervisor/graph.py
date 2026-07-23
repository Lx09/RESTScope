"""Round-based FIFO Supervisor for dynamically discovered operations."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from restscope.capabilities import ToolContext
from restscope.openapi_parser.ir import OperationIR, SchemaIR
from restscope.observability import TracingRuntime

from ..operation_test import (
    OperationCandidate,
    OperationDependencyAnalyzer,
    OperationReference,
    OperationTestAgent,
    OperationTestFinding,
    OperationTestReport,
    OperationTestRequest,
    OperationTestRunner,
)
from .schemas import BlockedOperation, OperationAttempt, RESTScopeRunReport, RESTScopeRunRequest


class RESTScopeMainState(TypedDict, total=False):
    """Serializable scheduler state; ToolContext remains runtime-only."""

    request: dict[str, Any]
    operations: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    ready_queue: list[dict[str, Any]]
    blocked_queue: list[dict[str, Any]]
    satisfied: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    attempt_counts: dict[str, int]
    findings: list[dict[str, Any]]
    current_round: int
    rounds: int
    status: str
    stop_reason: str
    failed_operation: dict[str, Any]
    dependency_cycles: list[list[dict[str, Any]]]
    last_error: dict[str, Any]
    final_report: dict[str, Any]


class RESTScopeMainGraph:
    """Discover operations and schedule them through two round-based FIFO queues."""

    def __init__(
        self,
        *,
        operation_runner: OperationTestRunner,
        dependency_analyzer: OperationDependencyAnalyzer,
        tool_context: ToolContext,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.operation_runner = operation_runner
        self.dependency_analyzer = dependency_analyzer
        self.tool_context = tool_context
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        task_id = request.metadata.get("task_id")
        attributes = {"restscope.task_id": task_id} if task_id else {}
        with self.tracing_runtime.span(
            "RESTScopeMainGraph.run",
            kind="AGENT",
            input_value=request,
            attributes=attributes,
        ) as span:
            graph = self._build_graph(request=request)
            final_state = graph.invoke(
                {
                    "request": request.model_dump(mode="json"),
                    "operations": [],
                    "candidates": [],
                    "ready_queue": [],
                    "blocked_queue": [],
                    "satisfied": [],
                    "attempts": [],
                    "attempt_counts": {},
                    "findings": [],
                    "current_round": 0,
                    "rounds": 0,
                    "dependency_cycles": [],
                },
                config={"recursion_limit": 10_000},
            )
            report = RESTScopeRunReport.model_validate(final_state["final_report"])
            span.set_output(report)
            span.set_attribute("restscope.run.status", report.status)
            return report

    def _build_graph(self, *, request: RESTScopeRunRequest):
        graph = StateGraph(RESTScopeMainState)
        graph.add_node("validate_runtime", self._validate_runtime(request))
        graph.add_node("discover_operations", self._discover_operations)
        graph.add_node("run_next_operation", self._run_next_operation(request))
        graph.add_node("advance_round", self._advance_round)
        graph.add_node("finalize_report", self._finalize_report)

        graph.add_edge(START, "validate_runtime")
        graph.add_conditional_edges(
            "validate_runtime",
            self._route_after_setup,
            {"finalize": "finalize_report", "next": "discover_operations"},
        )
        graph.add_conditional_edges(
            "discover_operations",
            self._route_after_discovery,
            {"finalize": "finalize_report", "run": "run_next_operation"},
        )
        graph.add_conditional_edges(
            "run_next_operation",
            self._route_after_attempt,
            {"finalize": "finalize_report", "run": "run_next_operation", "advance": "advance_round"},
        )
        graph.add_conditional_edges(
            "advance_round",
            self._route_after_advance,
            {"finalize": "finalize_report", "run": "run_next_operation"},
        )
        graph.add_edge("finalize_report", END)
        return graph.compile()

    def _validate_runtime(self, request: RESTScopeRunRequest):
        def node(state: RESTScopeMainState) -> RESTScopeMainState:
            del state
            try:
                self.dependency_analyzer.check_configured()
                return {}
            except Exception as exc:
                return self._technical_error(exc, stage="validate_runtime")

        return node

    def _discover_operations(self, state: RESTScopeMainState) -> RESTScopeMainState:
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
            candidates = [_operation_candidate(operation) for operation in ordered_ir]
            operations = [candidate.operation for candidate in candidates]
            return {
                "operations": [operation.model_dump(mode="json") for operation in operations],
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                "ready_queue": [operation.model_dump(mode="json") for operation in operations],
                "current_round": 1,
                "rounds": 1,
            }
        except Exception as exc:
            return self._technical_error(exc, stage="discover_operations")

    def _run_next_operation(self, request: RESTScopeRunRequest):
        def node(state: RESTScopeMainState) -> RESTScopeMainState:
            ready = list(state.get("ready_queue", []))
            if not ready:
                return {}
            operation = OperationReference.model_validate(ready.pop(0))
            candidates = [OperationCandidate.model_validate(item) for item in state.get("candidates", [])]
            counts = dict(state.get("attempt_counts", {}))
            identity_key = _identity_key(operation)
            attempt_number = counts.get(identity_key, 0) + 1
            counts[identity_key] = attempt_number

            try:
                report = OperationTestAgent(
                    runner=self.operation_runner,
                    dependency_analyzer=self.dependency_analyzer,
                    tracing_runtime=self.tracing_runtime,
                ).run(
                    OperationTestRequest(
                        task_id=request.metadata.get("task_id"),
                        operation=operation,
                        candidate_operations=candidates,
                    )
                )
            except Exception as exc:
                return {
                    "ready_queue": ready,
                    "attempt_counts": counts,
                    **self._technical_error(exc, stage="run_next_operation", operation=operation),
                }

            analysis = report.dependency_analysis
            direct_dependencies = analysis.dependencies if analysis is not None else []
            satisfied_ids = {
                OperationReference.model_validate(item).identity()
                for item in state.get("satisfied", [])
            }
            unsatisfied = [
                dependency
                for dependency in direct_dependencies
                if dependency.identity() not in satisfied_ids
            ]
            disposition = "errored"
            updates: RESTScopeMainState = {}

            if report.status == "errored":
                updates.update(
                    {
                        "status": "errored",
                        "stop_reason": "technical_error",
                        "last_error": {
                            "stage": "run_next_operation",
                            "type": "OperationTestError",
                            "message": "Operation test attempt returned an errored report",
                            "operation": operation.model_dump(mode="json"),
                            "operation_error": report.error,
                        },
                    }
                )
            elif analysis is not None and analysis.dependency_issue and (not direct_dependencies or unsatisfied):
                disposition = "blocked"
                blocked = list(state.get("blocked_queue", []))
                blocked.append(
                    {
                        "operation": operation.model_dump(mode="json"),
                        "dependency_hint": analysis.hint,
                        "direct_dependencies": [item.model_dump(mode="json") for item in direct_dependencies],
                        "unsatisfied_dependencies": [item.model_dump(mode="json") for item in unsatisfied],
                    }
                )
                updates["blocked_queue"] = blocked
            elif report.status == "passed" and report.observed_2xx:
                disposition = "satisfied"
                updates["satisfied"] = list(state.get("satisfied", [])) + [operation.model_dump(mode="json")]
            else:
                disposition = "failed"
                updates.update(
                    {
                        "status": "failed",
                        "stop_reason": "operation_failed",
                        "failed_operation": operation.model_dump(mode="json"),
                    }
                )

            attempt = OperationAttempt(
                operation=operation,
                round_number=int(state.get("current_round", 1)),
                attempt_number=attempt_number,
                report=report,
                disposition=disposition,
                dependency_hint=analysis.hint if analysis is not None else None,
                direct_dependencies=direct_dependencies,
                unsatisfied_dependencies=unsatisfied,
            )
            updates.update(
                {
                    "ready_queue": ready,
                    "attempt_counts": counts,
                    "attempts": list(state.get("attempts", [])) + [attempt.model_dump(mode="json")],
                    "findings": list(state.get("findings", []))
                    + [finding.model_dump(mode="json") for finding in report.findings],
                }
            )
            return updates

        return node

    def _advance_round(self, state: RESTScopeMainState) -> RESTScopeMainState:
        blocked = list(state.get("blocked_queue", []))
        if not blocked:
            return {"status": "passed", "stop_reason": "completed"}

        satisfied_ids = {
            OperationReference.model_validate(item).identity()
            for item in state.get("satisfied", [])
        }
        promotable: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for item in blocked:
            dependencies = [OperationReference.model_validate(value) for value in item.get("direct_dependencies", [])]
            if dependencies and all(dependency.identity() in satisfied_ids for dependency in dependencies):
                promotable.append(item)
            else:
                remaining.append(item)

        if not promotable:
            cycles = _dependency_cycles(remaining)
            return {
                "blocked_queue": remaining,
                "dependency_cycles": [
                    [operation.model_dump(mode="json") for operation in cycle]
                    for cycle in cycles
                ],
                "status": "failed",
                "stop_reason": "unresolved_dependencies",
            }

        operation_order = {
            OperationReference.model_validate(value).identity(): index
            for index, value in enumerate(state.get("operations", []))
        }
        promoted_operations = [OperationReference.model_validate(item["operation"]) for item in promotable]
        promoted_operations.sort(key=lambda operation: operation_order[operation.identity()])
        return {
            "ready_queue": [operation.model_dump(mode="json") for operation in promoted_operations],
            "blocked_queue": remaining,
            "current_round": int(state.get("current_round", 1)) + 1,
            "rounds": int(state.get("rounds", 1)) + 1,
        }

    def _finalize_report(self, state: RESTScopeMainState) -> RESTScopeMainState:
        operations = [OperationReference.model_validate(item) for item in state.get("operations", [])]
        attempts = [OperationAttempt.model_validate(item) for item in state.get("attempts", [])]
        satisfied_ids = {
            OperationReference.model_validate(item).identity()
            for item in state.get("satisfied", [])
        }
        satisfied = [operation for operation in operations if operation.identity() in satisfied_ids]
        attempted_ids = {attempt.operation.identity() for attempt in attempts}
        unattempted = [operation for operation in operations if operation.identity() not in attempted_ids]
        cycles = [
            [OperationReference.model_validate(operation) for operation in cycle]
            for cycle in state.get("dependency_cycles", [])
        ]
        cycle_ids = {operation.identity() for cycle in cycles for operation in cycle}
        failed_payload = state.get("failed_operation")
        failed_id = OperationReference.model_validate(failed_payload).identity() if failed_payload else None

        blocked_operations: list[BlockedOperation] = []
        for item in state.get("blocked_queue", []):
            operation = OperationReference.model_validate(item["operation"])
            direct_dependencies = [
                OperationReference.model_validate(value)
                for value in item.get("direct_dependencies", [])
            ]
            unsatisfied = [
                dependency for dependency in direct_dependencies if dependency.identity() not in satisfied_ids
            ]
            if not direct_dependencies:
                reason = "unknown_dependency"
            elif failed_id is not None and any(dependency.identity() == failed_id for dependency in unsatisfied):
                reason = "failed_prerequisite"
            elif operation.identity() in cycle_ids:
                reason = "dependency_cycle"
            else:
                reason = "unsatisfied_dependency"
            blocked_operations.append(
                BlockedOperation(
                    operation=operation,
                    dependency_hint=item.get("dependency_hint"),
                    direct_dependencies=direct_dependencies,
                    unsatisfied_dependencies=unsatisfied,
                    reason=reason,
                )
            )

        findings = [OperationTestFinding.model_validate(item) for item in state.get("findings", [])]
        run_ids = [run_id for attempt in attempts for run_id in attempt.report.run_ids]
        artifact_refs = [artifact for attempt in attempts for artifact in attempt.report.artifact_refs]
        error = state.get("last_error")
        status = "errored" if error is not None else state.get("status", "failed")
        stop_reason = "technical_error" if error is not None else state.get("stop_reason", "technical_error")
        report = RESTScopeRunReport(
            report_id=f"restscope_run_{uuid4().hex}",
            status=status,
            stop_reason=stop_reason,
            operations=operations,
            attempts=attempts,
            satisfied_operations=satisfied,
            blocked_operations=blocked_operations,
            unattempted_operations=unattempted,
            dependency_cycles=cycles,
            findings=findings,
            run_ids=_unique(run_ids),
            artifact_refs=_unique_artifact_refs(artifact_refs),
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
    def _route_after_setup(state: RESTScopeMainState) -> str:
        return "finalize" if state.get("last_error") else "next"

    @staticmethod
    def _route_after_discovery(state: RESTScopeMainState) -> str:
        return "finalize" if state.get("last_error") else "run"

    @staticmethod
    def _route_after_attempt(state: RESTScopeMainState) -> str:
        if state.get("status") in {"failed", "errored"}:
            return "finalize"
        return "run" if state.get("ready_queue") else "advance"

    @staticmethod
    def _route_after_advance(state: RESTScopeMainState) -> str:
        return "finalize" if state.get("status") in {"passed", "failed", "errored"} else "run"

    @staticmethod
    def _technical_error(
        exc: Exception,
        *,
        stage: str,
        operation: OperationReference | None = None,
    ) -> RESTScopeMainState:
        payload: dict[str, Any] = {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
        if operation is not None:
            payload["operation"] = operation.model_dump(mode="json")
        return {"status": "errored", "stop_reason": "technical_error", "last_error": payload}


def _path_depth(path: str) -> int:
    return sum(1 for segment in path.split("/") if segment)


def _operation_candidate(operation: OperationIR) -> OperationCandidate:
    parameters = []
    for location, values in (
        ("path", operation.path_parameters),
        ("query", operation.query_parameters),
        ("header", operation.header_parameters),
        ("cookie", operation.cookie_parameters),
    ):
        for parameter in values:
            parameters.append(
                {
                    "name": parameter.name,
                    "in": location,
                    "required": parameter.required,
                    "schema": _schema_structure(parameter.schema),
                }
            )

    request_structure = None
    if operation.request_body is not None:
        request_structure = {
            "required": operation.request_body.required,
            "content": {
                media_type: _schema_structure(media.schema)
                for media_type, media in operation.request_body.contents.items()
            },
        }
    response_structure = {
        status_code: {
            "content": {
                media_type: _schema_structure(media.schema)
                for media_type, media in response.contents.items()
            }
        }
        for status_code, response in operation.responses.by_status.items()
    }
    security = [
        {"scheme": requirement.scheme_name, "scopes": requirement.scopes}
        for requirement in operation.security.requirements
    ]
    return OperationCandidate(
        operation=OperationReference(
            method=operation.method,
            path=operation.path,
            operation_id=operation.operation_id,
        ),
        summary=operation.summary,
        parameters=parameters,
        security=security,
        request_structure=request_structure,
        response_structure=response_structure,
    )


def _schema_structure(schema: SchemaIR | None, *, depth: int = 0, seen: set[int] | None = None) -> dict[str, Any]:
    if schema is None:
        return {}
    if depth >= 8:
        return {"type": schema.type}
    seen = set() if seen is None else set(seen)
    if id(schema) in seen:
        return {"type": schema.type, "recursive": True}
    seen.add(id(schema))
    result: dict[str, Any] = {}
    if schema.type is not None:
        result["type"] = schema.type
    if schema.format is not None:
        result["format"] = schema.format
    if schema.required:
        result["required"] = list(schema.required)
    if schema.properties:
        result["properties"] = {
            name: _schema_structure(value, depth=depth + 1, seen=seen)
            for name, value in schema.properties.items()
        }
    if schema.items is not None:
        result["items"] = _schema_structure(schema.items, depth=depth + 1, seen=seen)
    for name in ("minimum", "maximum", "min_length", "max_length", "pattern", "min_items", "max_items"):
        value = getattr(schema, name)
        if value is not None:
            result[name] = value
    return result


def _identity_key(operation: OperationReference) -> str:
    return f"{operation.method}\0{operation.path}\0{operation.operation_id or ''}"


def _dependency_cycles(blocked: list[dict[str, Any]]) -> list[list[OperationReference]]:
    references: dict[tuple[str, str, str | None], OperationReference] = {}
    for item in blocked:
        operation = OperationReference.model_validate(item["operation"])
        references[operation.identity()] = operation
    graph = {
        identity: [
            dependency.identity()
            for dependency in (
                OperationReference.model_validate(value)
                for value in item.get("direct_dependencies", [])
            )
            if dependency.identity() in references
        ]
        for item in blocked
        for identity in [OperationReference.model_validate(item["operation"]).identity()]
    }
    visiting: set[tuple[str, str, str | None]] = set()
    visited: set[tuple[str, str, str | None]] = set()
    stack: list[tuple[str, str, str | None]] = []
    cycles: list[list[OperationReference]] = []
    seen_cycles: set[tuple[tuple[str, str, str | None], ...]] = set()

    def visit(node: tuple[str, str, str | None]) -> None:
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for dependency in graph.get(node, []):
            if dependency in visiting:
                start = stack.index(dependency)
                raw_cycle = stack[start:]
                rotations = [tuple(raw_cycle[index:] + raw_cycle[:index]) for index in range(len(raw_cycle))]
                canonical = min(rotations, key=repr)
                if canonical not in seen_cycles:
                    seen_cycles.add(canonical)
                    cycles.append([references[identity] for identity in raw_cycle])
            elif dependency not in visited:
                visit(dependency)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for identity in graph:
        visit(identity)
    return cycles


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _unique_artifact_refs(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[dict[str, Any]] = []
    for item in values:
        key = tuple(sorted((str(name), str(value)) for name, value in item.items()))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
