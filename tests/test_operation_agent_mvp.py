from __future__ import annotations

import json

import pytest


def _candidate(method: str = "GET", path: str = "/pets", operation_id: str | None = "listPets"):
    from restscope.agent import OperationCandidate, OperationReference

    return OperationCandidate(
        operation=OperationReference(method=method, path=path, operation_id=operation_id),
        summary="Compact operation summary",
        parameters=[{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        response_structure={"200": {"type": "object"}},
    )


def test_operation_test_agent_runs_schemathesis_once_and_analyzes_every_attempt() -> None:
    from restscope.agent import (
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        OperationTestAgent,
        OperationTestRequest,
    )

    runner = FakeOperationTestRunner()
    analyzer = FakeOperationDependencyAnalyzer()
    candidate = _candidate()
    report = OperationTestAgent(runner=runner, dependency_analyzer=analyzer).run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            operation=candidate.operation,
            candidate_operations=[candidate],
            headers={"Authorization": "Bearer secret-token"},
            allow_live_testing=True,
        )
    )

    assert report.status == "passed"
    assert report.observed_2xx is True
    assert len(runner.calls) == 1
    assert analyzer.calls == [candidate.operation]
    assert report.metadata["schemathesis_run_count"] == 1
    assert "stages" not in report.model_dump()
    assert "secret-token" not in report.model_dump_json()


def test_candidate_summary_contract_does_not_restore_inferred_graph_fields() -> None:
    from restscope.agent import OperationCandidate

    assert list(OperationCandidate.model_fields) == [
        "operation",
        "summary",
        "parameters",
        "security",
        "request_structure",
        "response_structure",
    ]


def test_operation_test_agent_records_outcome_and_2xx_independently() -> None:
    from restscope.agent import (
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        OperationExecutionResult,
        OperationTestAgent,
        OperationTestRequest,
    )

    candidate = _candidate()
    runner = FakeOperationTestRunner(
        results={
            ("GET", "/pets"): OperationExecutionResult(
                run_id="run_failed",
                outcome="failed",
                status_code_counts={"201": 1, "500": 1},
                failure_ids=["failure_1"],
            )
        }
    )
    report = OperationTestAgent(
        runner=runner,
        dependency_analyzer=FakeOperationDependencyAnalyzer(),
    ).run(
        OperationTestRequest(
            schema_source={"kind": "inline", "format": "json", "content": "{}"},
            operation=candidate.operation,
            candidate_operations=[candidate],
            allow_live_testing=True,
        )
    )

    assert report.status == "failed"
    assert report.observed_2xx is True
    assert report.execution is not None
    assert report.execution.outcome == "failed"
    assert report.findings[0].evidence_refs == ["failure_1"]


def test_dependency_analysis_error_preserves_the_completed_execution() -> None:
    from restscope.agent import (
        DependencyAnalysisError,
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        OperationTestAgent,
        OperationTestRequest,
    )

    candidate = _candidate()
    report = OperationTestAgent(
        runner=FakeOperationTestRunner(),
        dependency_analyzer=FakeOperationDependencyAnalyzer(error=DependencyAnalysisError("invalid output")),
    ).run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "api.yaml"},
            operation=candidate.operation,
            candidate_operations=[candidate],
            allow_live_testing=True,
        )
    )

    assert report.status == "errored"
    assert report.error is not None
    assert report.error["stage"] == "analyze_dependencies"
    assert report.execution is not None


def test_missing_dependency_model_fails_before_any_live_request() -> None:
    from restscope.agent import (
        DependencyAnalysisError,
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        OperationTestAgent,
        OperationTestRequest,
    )

    candidate = _candidate()
    runner = FakeOperationTestRunner()
    report = OperationTestAgent(
        runner=runner,
        dependency_analyzer=FakeOperationDependencyAnalyzer(
            config_error=DependencyAnalysisError("Thinking model is not configured")
        ),
    ).run(
        OperationTestRequest(
            schema_source={"kind": "file", "path": "api.yaml"},
            operation=candidate.operation,
            candidate_operations=[candidate],
            allow_live_testing=True,
        )
    )

    assert report.status == "errored"
    assert report.error is not None
    assert report.error["stage"] == "load_operation"
    assert runner.calls == []


def test_operation_test_request_requires_current_operation_in_candidates() -> None:
    from pydantic import ValidationError
    from restscope.agent import OperationReference, OperationTestRequest

    with pytest.raises(ValidationError):
        OperationTestRequest(
            schema_source={"kind": "file", "path": "api.yaml"},
            operation=OperationReference(method="GET", path="/missing"),
            candidate_operations=[_candidate()],
        )


class StubLLMClient:
    def __init__(self, response) -> None:
        self.response = response
        self.requests = []

    def invoke(self, request):
        self.requests.append(request)
        return self.response


def _model_config():
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_dependency_analyzer",
        provider="fake",
        model="thinking-test",
        temperature=0.7,
        max_tokens=1000,
        enabled=True,
    )


def test_llm_dependency_analyzer_accepts_exact_direct_dependency_and_uses_safe_prompt() -> None:
    from restscope.agent import (
        FailureSummary,
        LLMOperationDependencyAnalyzer,
        OperationExecutionResult,
    )
    from restscope.llm import LLMResponse

    current = _candidate("GET", "/pets/{id}", "getPet")
    dependency = _candidate("POST", "/pets", "createPet")
    client = StubLLMClient(
        LLMResponse(
            provider="fake",
            model="thinking-test",
            parsed_json={
                "dependency_issue": True,
                "hint": "Create a pet first",
                "dependencies": [dependency.operation.model_dump()],
            },
        )
    )
    analyzer = LLMOperationDependencyAnalyzer(client=client, model=_model_config())
    execution = OperationExecutionResult(
        run_id="run_1",
        outcome="failed",
        status_code_counts={"404": 1},
        failure_summaries=[
            FailureSummary(
                failure_id=f"failure_{index}",
                check="status_code_conformance",
                title="Unexpected status",
                message="not found",
                response_status=404,
            )
            for index in range(25)
        ],
    )

    analysis = analyzer.analyze(
        operation=current.operation,
        candidates=[current, dependency],
        execution=execution,
    )

    assert analysis.dependencies == [dependency.operation]
    [request] = client.requests
    assert request.temperature == 0
    assert request.tools == []
    assert request.tool_choice == "none"
    prompt = json.loads(request.messages[1].content)
    assert len(prompt["schemathesis"]["failures"]) == 20
    serialized = request.messages[1].content.lower()
    assert "authorization" not in serialized
    assert "curl" not in serialized
    assert "body" not in serialized


@pytest.mark.parametrize(
    "payload,error_fragment",
    [
        (
            {
                "dependency_issue": True,
                "hint": "self",
                "dependencies": [
                    {"method": "GET", "path": "/pets", "operation_id": "listPets"}
                ],
            },
            "self dependency",
        ),
        (
            {
                "dependency_issue": True,
                "hint": "unknown",
                "dependencies": [
                    {"method": "POST", "path": "/unknown", "operation_id": None}
                ],
            },
            "unknown operation",
        ),
    ],
)
def test_llm_dependency_analyzer_rejects_self_and_unknown_operations(payload, error_fragment) -> None:
    from restscope.agent import DependencyAnalysisError, LLMOperationDependencyAnalyzer, OperationExecutionResult
    from restscope.llm import LLMResponse

    current = _candidate()
    analyzer = LLMOperationDependencyAnalyzer(
        client=StubLLMClient(LLMResponse(provider="fake", model="thinking-test", parsed_json=payload)),
        model=_model_config(),
    )

    with pytest.raises(DependencyAnalysisError, match=error_fragment):
        analyzer.analyze(
            operation=current.operation,
            candidates=[current],
            execution=OperationExecutionResult(run_id="run_1", outcome="failed"),
        )


def test_llm_dependency_analyzer_does_not_repair_invalid_json() -> None:
    from restscope.agent import DependencyAnalysisError, LLMOperationDependencyAnalyzer, OperationExecutionResult
    from restscope.llm import LLMResponse

    current = _candidate()
    analyzer = LLMOperationDependencyAnalyzer(
        client=StubLLMClient(LLMResponse(provider="fake", model="thinking-test", content="not-json")),
        model=_model_config(),
    )

    with pytest.raises(DependencyAnalysisError, match="Invalid dependency analysis output"):
        analyzer.analyze(
            operation=current.operation,
            candidates=[current],
            execution=OperationExecutionResult(run_id="run_1", outcome="failed"),
        )
