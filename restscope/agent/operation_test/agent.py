"""LangGraph scaffold for testing one OpenAPI operation."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session, sessionmaker

from restscope.db import UnitOfWork

from .runner import OperationTestRunner
from .schemas import (
    OperationTarget,
    OperationTestFinding,
    OperationTestReport,
    OperationTestRequest,
    OperationTestStageResult,
    StageOptions,
)
from .stages import OperationTestStage, default_operation_test_stages


class OperationTestState(TypedDict, total=False):
    """Lightweight graph state for the single-operation test workflow."""

    request: dict[str, Any]
    target: dict[str, Any]
    options: dict[str, Any]
    capabilities: dict[str, Any]
    pending_stages: list[str]
    completed_stages: list[str]
    stage_results: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    status: str
    last_error: dict[str, Any]
    final_report: dict[str, Any]


class OperationTestAgent:
    """Run smoke, conformance, positive, negative, and boundary stages."""

    def __init__(
        self,
        *,
        runner: OperationTestRunner,
        session_factory: sessionmaker[Session] | None = None,
        stages: list[OperationTestStage] | None = None,
    ) -> None:
        self.runner = runner
        self.session_factory = session_factory
        self.stages = stages or default_operation_test_stages()
        self._stage_by_name = {stage.name: stage for stage in self.stages}

    def run(self, request: OperationTestRequest) -> OperationTestReport:
        """Execute the graph and return a sanitized operation test report."""

        runtime_headers = dict(request.headers)
        graph = self._build_graph(runtime_headers=runtime_headers)
        initial_state: OperationTestState = {
            "request": request.model_dump(exclude={"headers"}),
            "pending_stages": [stage.name for stage in self.stages],
            "completed_stages": [],
            "stage_results": [],
            "findings": [],
        }
        final_state = graph.invoke(initial_state)
        return OperationTestReport.model_validate(final_state["final_report"])

    def _build_graph(self, *, runtime_headers: dict[str, str]):
        graph = StateGraph(OperationTestState)
        graph.add_node("load_operation", self._load_operation)
        graph.add_node("check_capabilities", self._check_capabilities)
        graph.add_node("run_stage", self._run_stage(runtime_headers=runtime_headers))
        graph.add_node("evaluate_results", self._evaluate_results)
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
            {"fail": "fail", "next": "run_stage"},
        )
        graph.add_conditional_edges(
            "run_stage",
            self._route_after_stage,
            {"fail": "fail", "run_stage": "run_stage", "evaluate_results": "evaluate_results"},
        )
        graph.add_edge("evaluate_results", "finalize_report")
        graph.add_edge("finalize_report", END)
        graph.add_edge("fail", END)
        return graph.compile()

    def _load_operation(self, state: OperationTestState) -> OperationTestState:
        try:
            request = OperationTestRequest.model_validate(state["request"])
            if request.schema_source is not None and request.method is not None and request.path is not None:
                target = OperationTarget(
                    schema_source=request.schema_source,
                    base_url=request.base_url,
                    method=request.method,
                    path=request.path,
                    operation_id=request.operation_id,
                    schema_id=request.schema_id,
                    operation_db_id=request.operation_db_id,
                )
            else:
                target = self._load_target_from_db(request)

            options = StageOptions(
                max_examples=request.max_examples,
                boundary_max_examples=request.boundary_max_examples,
                max_failures=request.max_failures,
                max_time=request.max_time,
                poll_interval=request.poll_interval,
                poll_timeout=request.poll_timeout,
                seed=request.seed,
            )
            return {"target": target.model_dump(exclude={"headers"}), "options": options.model_dump()}
        except Exception as exc:
            return {"last_error": self._error_payload(exc, stage="load_operation")}

    def _load_target_from_db(self, request: OperationTestRequest) -> OperationTarget:
        if self.session_factory is None:
            raise RuntimeError("DB-backed operation input requires session_factory")
        if request.schema_id is None or request.operation_db_id is None:
            raise RuntimeError("DB-backed operation input requires schema_id and operation_db_id")

        with UnitOfWork(self.session_factory) as uow:
            schema = uow.schemas.require(request.schema_id)
            operation = uow.operations.require(request.operation_db_id)

        if operation.schema_id != schema.id:
            raise RuntimeError(f"Operation {operation.id} does not belong to schema {schema.id}")

        return OperationTarget(
            schema_source=_schema_source_from_uri(schema.raw_spec_uri),
            base_url=request.base_url,
            method=operation.method,
            path=operation.path,
            operation_id=operation.operation_id,
            schema_id=schema.id,
            operation_db_id=operation.id,
        )

    def _check_capabilities(self, state: OperationTestState) -> OperationTestState:
        try:
            target = OperationTarget.model_validate(state["target"])
            capabilities = self.runner.check_capabilities(
                target=target,
                state=self._runner_state(state),
            )
            return {"capabilities": capabilities}
        except Exception as exc:
            return {"last_error": self._error_payload(exc, stage="check_capabilities")}

    def _run_stage(self, *, runtime_headers: dict[str, str]):
        def node(state: OperationTestState) -> OperationTestState:
            pending = list(state.get("pending_stages", []))
            if not pending:
                return {}
            stage_name = pending.pop(0)
            try:
                stage = self._stage_by_name[stage_name]
                target = OperationTarget.model_validate(state["target"]).model_copy(
                    update={"headers": runtime_headers}
                )
                result = self.runner.run_stage(
                    stage=stage,
                    target=target,
                    options=StageOptions.model_validate(state["options"]),
                    state=self._runner_state(state),
                )
                completed = list(state.get("completed_stages", [])) + [stage_name]
                results = list(state.get("stage_results", [])) + [result.model_dump()]
                return {
                    "pending_stages": pending,
                    "completed_stages": completed,
                    "stage_results": results,
                }
            except Exception as exc:
                return {
                    "pending_stages": pending,
                    "last_error": self._error_payload(exc, stage=stage_name),
                }

        return node

    def _evaluate_results(self, state: OperationTestState) -> OperationTestState:
        findings: list[OperationTestFinding] = []
        status = "passed"
        for payload in state.get("stage_results", []):
            result = OperationTestStageResult.model_validate(payload)
            findings.extend(result.findings)
            if result.status == "failed":
                status = "failed"
                if not result.findings:
                    findings.append(
                        OperationTestFinding(
                            stage=result.stage,
                            severity="high",
                            title=f"{result.stage} failures",
                            summary=f"{result.stage} returned a failed result.",
                            evidence_refs=result.failure_ids,
                            artifact_refs=result.artifact_refs,
                        )
                    )
            elif result.status == "errored":
                status = "errored"

        return {
            "status": status,
            "findings": [finding.model_dump() for finding in findings],
        }

    def _finalize_report(self, state: OperationTestState) -> OperationTestState:
        return {"final_report": self._report_payload(state, error=None)}

    def _fail(self, state: OperationTestState) -> OperationTestState:
        return {"final_report": self._report_payload(state, error=state.get("last_error"))}

    def _report_payload(self, state: OperationTestState, *, error: dict[str, Any] | None) -> dict[str, Any]:
        target_payload = state.get("target") or {}
        target = OperationTarget.model_validate(target_payload) if target_payload else None
        stage_results = [OperationTestStageResult.model_validate(item) for item in state.get("stage_results", [])]
        findings = [OperationTestFinding.model_validate(item) for item in state.get("findings", [])]
        status = "errored" if error is not None else state.get("status", "passed")

        artifact_refs: list[dict[str, Any]] = []
        run_ids: list[str] = []
        for result in stage_results:
            if result.run_id is not None:
                run_ids.append(result.run_id)
            artifact_refs.extend(result.artifact_refs)
        for finding in findings:
            artifact_refs.extend(finding.artifact_refs)

        request = state.get("request", {})
        return OperationTestReport(
            report_id=f"optest_report_{uuid4().hex}",
            status=status,
            task_id=request.get("task_id"),
            schema_id=target.schema_id if target else request.get("schema_id"),
            operation_db_id=target.operation_db_id if target else request.get("operation_db_id"),
            operation_id=target.operation_id if target else request.get("operation_id"),
            method=target.method if target else request.get("method"),
            path=target.path if target else request.get("path"),
            stages=stage_results,
            findings=findings,
            run_ids=_unique(run_ids),
            artifact_refs=_unique_artifact_refs(artifact_refs),
            error=error,
            metadata={
                "agent": "operation_test_agent",
                "stage_count": len(stage_results),
            },
        ).model_dump()

    def _runner_state(self, state: OperationTestState) -> dict[str, Any]:
        request = state.get("request", {})
        return {
            "task_id": request.get("task_id"),
            "allow_live_testing": bool(request.get("allow_live_testing")),
            "schema_id": request.get("schema_id"),
            "operation_db_id": request.get("operation_db_id"),
        }

    def _route_after_error(self, state: OperationTestState) -> str:
        return "fail" if state.get("last_error") else "next"

    def _route_after_stage(self, state: OperationTestState) -> str:
        if state.get("last_error"):
            return "fail"
        return "run_stage" if state.get("pending_stages") else "evaluate_results"

    def _error_payload(self, exc: Exception, *, stage: str) -> dict[str, Any]:
        return {
            "stage": stage,
            "type": type(exc).__name__,
            "message": str(exc),
        }


def _schema_source_from_uri(uri: str) -> dict[str, str]:
    if uri.startswith(("http://", "https://")):
        return {"kind": "url", "url": uri}
    if uri.startswith("file://"):
        return {"kind": "file", "path": uri.removeprefix("file://")}
    return {"kind": "file", "path": uri}


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
