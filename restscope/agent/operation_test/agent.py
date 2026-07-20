"""LangGraph workflow for one complete operation-test attempt."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .dependency import OperationDependencyAnalyzer
from .runner import OperationTestRunner
from .schemas import (
    OperationDependencyAnalysis,
    OperationExecutionResult,
    OperationTarget,
    OperationTestFinding,
    OperationTestReport,
    OperationTestRequest,
)


class OperationTestState(TypedDict, total=False):
    """Serializable attempt state; runtime headers stay outside this object."""

    request: dict[str, Any]
    target: dict[str, Any]
    capabilities: dict[str, Any]
    execution: dict[str, Any]
    dependency_analysis: dict[str, Any]
    findings: list[dict[str, Any]]
    status: str
    last_error: dict[str, Any]
    final_report: dict[str, Any]


class OperationTestAgent:
    """Run Schemathesis once, then analyze direct operation dependencies."""

    def __init__(
        self,
        *,
        runner: OperationTestRunner,
        dependency_analyzer: OperationDependencyAnalyzer,
    ) -> None:
        self.runner = runner
        self.dependency_analyzer = dependency_analyzer

    def run(self, request: OperationTestRequest) -> OperationTestReport:
        runtime_headers = dict(request.headers)
        graph = self._build_graph(runtime_headers=runtime_headers)
        initial_state: OperationTestState = {
            "request": request.model_dump(exclude={"headers"}, mode="json"),
            "findings": [],
        }
        final_state = graph.invoke(initial_state)
        return OperationTestReport.model_validate(final_state["final_report"])

    def _build_graph(self, *, runtime_headers: dict[str, str]):
        graph = StateGraph(OperationTestState)
        graph.add_node("load_operation", self._load_operation)
        graph.add_node("check_capabilities", self._check_capabilities)
        graph.add_node("run_operation", self._run_operation(runtime_headers=runtime_headers))
        graph.add_node("analyze_dependencies", self._analyze_dependencies)
        graph.add_node("evaluate_result", self._evaluate_result)
        graph.add_node("finalize_report", self._finalize_report)
        graph.add_node("fail", self._fail)

        graph.add_edge(START, "load_operation")
        graph.add_conditional_edges(
            "load_operation",
            self._route_after_error,
            {"fail": "fail", "next": "check_capabilities"},
        )
        graph.add_conditional_edges(
            "check_capabilities",
            self._route_after_error,
            {"fail": "fail", "next": "run_operation"},
        )
        graph.add_conditional_edges(
            "run_operation",
            self._route_after_error,
            {"fail": "fail", "next": "analyze_dependencies"},
        )
        graph.add_conditional_edges(
            "analyze_dependencies",
            self._route_after_error,
            {"fail": "fail", "next": "evaluate_result"},
        )
        graph.add_edge("evaluate_result", "finalize_report")
        graph.add_edge("finalize_report", END)
        graph.add_edge("fail", END)
        return graph.compile()

    def _load_operation(self, state: OperationTestState) -> OperationTestState:
        try:
            request = OperationTestRequest.model_validate(state["request"])
            self.dependency_analyzer.check_configured()
            target = OperationTarget(
                schema_source=request.schema_source,
                base_url=request.base_url,
                operation=request.operation,
            )
            return {"target": target.model_dump(exclude={"headers"}, mode="json")}
        except Exception as exc:
            return {"last_error": self._error_payload(exc, stage="load_operation")}

    def _check_capabilities(self, state: OperationTestState) -> OperationTestState:
        try:
            capabilities = self.runner.check_capabilities(
                target=OperationTarget.model_validate(state["target"]),
                state=self._runner_state(state),
            )
            return {"capabilities": capabilities}
        except Exception as exc:
            return {"last_error": self._error_payload(exc, stage="check_capabilities")}

    def _run_operation(self, *, runtime_headers: dict[str, str]):
        def node(state: OperationTestState) -> OperationTestState:
            try:
                target = OperationTarget.model_validate(state["target"]).model_copy(
                    update={"headers": runtime_headers}
                )
                execution = self.runner.run_operation(target=target, state=self._runner_state(state))
                return {"execution": execution.model_dump(mode="json")}
            except Exception as exc:
                return {"last_error": self._error_payload(exc, stage="run_operation")}

        return node

    def _analyze_dependencies(self, state: OperationTestState) -> OperationTestState:
        try:
            request = OperationTestRequest.model_validate(state["request"])
            execution = OperationExecutionResult.model_validate(state["execution"])
            analysis = self.dependency_analyzer.analyze(
                operation=request.operation,
                candidates=request.candidate_operations,
                execution=execution,
            )
            return {"dependency_analysis": analysis.model_dump(mode="json")}
        except Exception as exc:
            return {"last_error": self._error_payload(exc, stage="analyze_dependencies")}

    def _evaluate_result(self, state: OperationTestState) -> OperationTestState:
        execution = OperationExecutionResult.model_validate(state["execution"])
        outcome = execution.outcome.lower()
        if outcome in {"passed", "success", "succeeded"}:
            status = "passed"
        elif outcome == "errored":
            status = "errored"
        else:
            status = "failed"
        findings: list[OperationTestFinding] = []
        if status == "failed":
            findings.append(
                OperationTestFinding(
                    severity="high",
                    title="Schemathesis reported operation failures",
                    summary=f"Schemathesis returned {execution.outcome} with {len(execution.failure_ids)} failure(s).",
                    evidence_refs=execution.failure_ids or [execution.run_id],
                    artifact_refs=execution.artifact_refs,
                )
            )
        return {"status": status, "findings": [finding.model_dump(mode="json") for finding in findings]}

    def _finalize_report(self, state: OperationTestState) -> OperationTestState:
        return {"final_report": self._report_payload(state, error=None)}

    def _fail(self, state: OperationTestState) -> OperationTestState:
        return {"final_report": self._report_payload(state, error=state.get("last_error"))}

    def _report_payload(self, state: OperationTestState, *, error: dict[str, Any] | None) -> dict[str, Any]:
        request = OperationTestRequest.model_validate(state["request"])
        execution_payload = state.get("execution")
        execution = OperationExecutionResult.model_validate(execution_payload) if execution_payload else None
        analysis_payload = state.get("dependency_analysis")
        analysis = OperationDependencyAnalysis.model_validate(analysis_payload) if analysis_payload else None
        findings = [OperationTestFinding.model_validate(item) for item in state.get("findings", [])]
        status = "errored" if error is not None else state.get("status", "passed")
        return OperationTestReport(
            report_id=f"optest_report_{uuid4().hex}",
            status=status,
            task_id=request.task_id,
            operation=request.operation,
            execution=execution,
            dependency_analysis=analysis,
            observed_2xx=execution.observed_2xx if execution else False,
            findings=findings,
            run_ids=[execution.run_id] if execution else [],
            artifact_refs=execution.artifact_refs if execution else [],
            error=error,
            metadata={"agent": "operation_test_agent", "schemathesis_run_count": 1 if execution else 0},
        ).model_dump(mode="json")

    @staticmethod
    def _runner_state(state: OperationTestState) -> dict[str, Any]:
        request = state.get("request", {})
        return {
            "task_id": request.get("task_id"),
            "allow_live_testing": bool(request.get("allow_live_testing")),
        }

    @staticmethod
    def _route_after_error(state: OperationTestState) -> str:
        return "fail" if state.get("last_error") else "next"

    @staticmethod
    def _error_payload(exc: Exception, *, stage: str) -> dict[str, Any]:
        return {"stage": stage, "type": type(exc).__name__, "message": str(exc)}
