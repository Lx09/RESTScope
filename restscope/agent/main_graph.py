"""Supervisor graph for RESTScope program runs."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session, sessionmaker

from .operation_test_agent import OperationTestAgent
from .runner import OperationTestRunner
from .schemas import (
    OperationSelection,
    OperationTestFinding,
    OperationTestReport,
    OperationTestRequest,
    RESTScopeRunReport,
    RESTScopeRunRequest,
)


class RESTScopeMainState(TypedDict, total=False):
    """Lightweight supervisor state; raw specs and secrets stay out."""

    request: dict[str, Any]
    selected_index: int
    operation_reports: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    status: str
    last_error: dict[str, Any]
    final_report: dict[str, Any]


class RESTScopeMainGraph:
    """Coordinate selected operation tests and aggregate their reports."""

    def __init__(
        self,
        *,
        operation_runner: OperationTestRunner,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.operation_runner = operation_runner
        self.session_factory = session_factory

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        """Run the supervisor graph for a direct request."""

        runtime_headers = dict(request.headers)
        graph = self._build_graph(runtime_headers=runtime_headers)
        final_state = graph.invoke(
            {
                "request": request.model_dump(exclude={"headers"}),
                "selected_index": 0,
                "operation_reports": [],
                "findings": [],
            }
        )
        return RESTScopeRunReport.model_validate(final_state["final_report"])

    def _build_graph(self, *, runtime_headers: dict[str, str]):
        graph = StateGraph(RESTScopeMainState)
        graph.add_node("classify_task", self._classify_task)
        graph.add_node("validate_permissions", self._validate_permissions)
        graph.add_node("resolve_operations", self._resolve_operations)
        graph.add_node("run_next_operation", self._run_next_operation(runtime_headers=runtime_headers))
        graph.add_node("aggregate_reports", self._aggregate_reports)
        graph.add_node("finalize_report", self._finalize_report)
        graph.add_node("fail", self._fail)

        graph.add_edge(START, "classify_task")
        graph.add_conditional_edges(
            "classify_task",
            self._route_after_error,
            {"fail": "fail", "next": "validate_permissions"},
        )
        graph.add_conditional_edges(
            "validate_permissions",
            self._route_after_error,
            {"fail": "fail", "next": "resolve_operations"},
        )
        graph.add_conditional_edges(
            "resolve_operations",
            self._route_after_error,
            {"fail": "fail", "next": "run_next_operation"},
        )
        graph.add_conditional_edges(
            "run_next_operation",
            self._route_after_operation,
            {"run_next_operation": "run_next_operation", "aggregate_reports": "aggregate_reports"},
        )
        graph.add_edge("aggregate_reports", "finalize_report")
        graph.add_edge("finalize_report", END)
        graph.add_edge("fail", END)
        return graph.compile()

    def _classify_task(self, state: RESTScopeMainState) -> RESTScopeMainState:
        try:
            request = RESTScopeRunRequest.model_validate(state["request"])
            if request.task_kind != "operation_test":
                raise RuntimeError(f"Unsupported task kind: {request.task_kind}")
            return {}
        except Exception as exc:
            return {"last_error": _error_payload(exc, stage="classify_task")}

    def _validate_permissions(self, state: RESTScopeMainState) -> RESTScopeMainState:
        request = RESTScopeRunRequest.model_validate(state["request"])
        if not request.allow_live_testing:
            return {
                "last_error": {
                    "stage": "validate_permissions",
                    "type": "PermissionError",
                    "message": "Operation testing requires allow_live_testing=True.",
                }
            }
        return {}

    def _resolve_operations(self, state: RESTScopeMainState) -> RESTScopeMainState:
        request = RESTScopeRunRequest.model_validate(state["request"])
        if not request.operations:
            return {
                "last_error": {
                    "stage": "resolve_operations",
                    "type": "ValueError",
                    "message": "RESTScopeRunRequest requires at least one selected operation.",
                }
            }
        return {}

    def _run_next_operation(self, *, runtime_headers: dict[str, str]):
        def node(state: RESTScopeMainState) -> RESTScopeMainState:
            request = RESTScopeRunRequest.model_validate(state["request"])
            selected_index = int(state.get("selected_index", 0))
            if selected_index >= len(request.operations):
                return {}

            operation = request.operations[selected_index]
            try:
                agent = OperationTestAgent(
                    runner=self.operation_runner,
                    session_factory=self.session_factory,
                )
                operation_report = agent.run(
                    OperationTestRequest(
                        schema_source=request.schema_source,
                        base_url=request.base_url,
                        method=operation.method,
                        path=operation.path,
                        operation_id=operation.operation_id,
                        headers=runtime_headers,
                        allow_live_testing=request.allow_live_testing,
                        max_examples=request.max_examples,
                        boundary_max_examples=request.boundary_max_examples,
                        max_failures=request.max_failures,
                        max_time=request.max_time,
                        poll_interval=request.poll_interval,
                        poll_timeout=request.poll_timeout,
                        seed=request.seed,
                    )
                )
            except Exception as exc:
                return {
                    "last_error": _error_payload(
                        exc,
                        stage="run_next_operation",
                        operation=operation,
                    )
                }

            if operation_report.status == "errored":
                return {
                    "last_error": {
                        "stage": "run_next_operation",
                        "type": "OperationTestError",
                        "message": "Operation test returned an errored report.",
                        "operation": operation.model_dump(),
                        "operation_error": operation_report.error,
                    }
                }

            reports = list(state.get("operation_reports", [])) + [operation_report.model_dump()]
            findings = list(state.get("findings", [])) + [
                finding.model_dump() for finding in operation_report.findings
            ]
            return {
                "selected_index": selected_index + 1,
                "operation_reports": reports,
                "findings": findings,
            }

        return node

    def _aggregate_reports(self, state: RESTScopeMainState) -> RESTScopeMainState:
        reports = [
            OperationTestReport.model_validate(item)
            for item in state.get("operation_reports", [])
        ]
        status = "passed"
        if state.get("last_error") is not None:
            status = "errored"
        elif any(report.status == "errored" for report in reports):
            status = "errored"
        elif any(report.status == "failed" for report in reports):
            status = "failed"

        findings = [OperationTestFinding.model_validate(item) for item in state.get("findings", [])]
        return {
            "status": status,
            "findings": [finding.model_dump() for finding in findings],
        }

    def _finalize_report(self, state: RESTScopeMainState) -> RESTScopeMainState:
        return {"final_report": self._report_payload(state, error=state.get("last_error"))}

    def _fail(self, state: RESTScopeMainState) -> RESTScopeMainState:
        return {"final_report": self._report_payload(state, error=state.get("last_error"))}

    def _route_after_error(self, state: RESTScopeMainState) -> str:
        return "fail" if state.get("last_error") else "next"

    def _route_after_operation(self, state: RESTScopeMainState) -> str:
        if state.get("last_error"):
            return "aggregate_reports"
        request = RESTScopeRunRequest.model_validate(state["request"])
        return "run_next_operation" if state.get("selected_index", 0) < len(request.operations) else "aggregate_reports"

    def _report_payload(self, state: RESTScopeMainState, *, error: dict[str, Any] | None) -> dict[str, Any]:
        request = RESTScopeRunRequest.model_validate(state["request"])
        reports = [OperationTestReport.model_validate(item) for item in state.get("operation_reports", [])]
        findings = [OperationTestFinding.model_validate(item) for item in state.get("findings", [])]

        run_ids: list[str] = []
        artifact_refs: list[dict[str, Any]] = []
        for report in reports:
            run_ids.extend(report.run_ids)
            artifact_refs.extend(report.artifact_refs)
        for finding in findings:
            artifact_refs.extend(finding.artifact_refs)

        return RESTScopeRunReport(
            report_id=f"restscope_run_{uuid4().hex}",
            status="errored" if error is not None else state.get("status", "passed"),
            task_kind=request.task_kind,
            operations=request.operations,
            operation_reports=reports,
            findings=findings,
            run_ids=_unique(run_ids),
            artifact_refs=_unique_artifact_refs(artifact_refs),
            error=error,
            metadata={
                "graph": "restscope_main_graph",
                "selected_operation_count": len(request.operations),
                "completed_operation_count": len(reports),
            },
        ).model_dump()


def _error_payload(
    exc: Exception,
    *,
    stage: str,
    operation: OperationSelection | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
    }
    if operation is not None:
        payload["operation"] = operation.model_dump()
    return payload


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _unique_artifact_refs(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[dict[str, Any]] = []
    for item in values:
        key = tuple(sorted((str(name), str(value)) for name, value in item.items()))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
