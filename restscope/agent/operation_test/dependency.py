"""Dependency analyzers used after every operation-test execution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from restscope.llm import LLMClient, LLMMessage, LLMModelConfig, LLMRequest, OutputValidator

from .schemas import (
    FailureSummary,
    OperationCandidate,
    OperationDependencyAnalysis,
    OperationExecutionResult,
    OperationReference,
)


class DependencyAnalysisError(RuntimeError):
    """Raised when dependency output is unavailable or invalid."""


class OperationDependencyAnalyzer(Protocol):
    """Analyze direct dependencies for one completed test attempt."""

    def check_configured(self) -> None: ...

    def analyze(
        self,
        *,
        operation: OperationReference,
        candidates: Sequence[OperationCandidate],
        execution: OperationExecutionResult,
    ) -> OperationDependencyAnalysis: ...


class FakeOperationDependencyAnalyzer:
    """Deterministic analyzer for graph and scheduler tests."""

    def __init__(
        self,
        *,
        analyses: dict[tuple[str, str], OperationDependencyAnalysis | list[OperationDependencyAnalysis]] | None = None,
        error: Exception | None = None,
        config_error: Exception | None = None,
    ) -> None:
        self.analyses = analyses or {}
        self.error = error
        self.config_error = config_error
        self.calls: list[OperationReference] = []

    def check_configured(self) -> None:
        if self.config_error is not None:
            raise self.config_error

    def analyze(
        self,
        *,
        operation: OperationReference,
        candidates: Sequence[OperationCandidate],
        execution: OperationExecutionResult,
    ) -> OperationDependencyAnalysis:
        del candidates, execution
        self.calls.append(operation)
        if self.error is not None:
            raise self.error
        configured = self.analyses.get((operation.method, operation.path))
        if isinstance(configured, list):
            if not configured:
                return OperationDependencyAnalysis(dependency_issue=False)
            return configured.pop(0)
        if configured is not None:
            return configured
        return OperationDependencyAnalysis(dependency_issue=False)


class LLMOperationDependencyAnalyzer:
    """Use the configured Thinking model for strict direct-dependency output."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()

    def check_configured(self) -> None:
        if not self.model.enabled or not self.model.provider or not self.model.model:
            raise DependencyAnalysisError("Thinking model is not configured")
        registry = getattr(self.client, "registry", None)
        if registry is not None:
            try:
                registry.get(self.model.provider)
            except Exception as exc:
                raise DependencyAnalysisError(
                    f"Thinking model provider is not configured: {self.model.provider}"
                ) from exc

    def analyze(
        self,
        *,
        operation: OperationReference,
        candidates: Sequence[OperationCandidate],
        execution: OperationExecutionResult,
    ) -> OperationDependencyAnalysis:
        self.check_configured()
        response = self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Determine whether the tested operation failed because prerequisite operations "
                            "have not established required state. Return direct dependencies only. Select "
                            "dependencies exclusively from the supplied candidates and never select the current "
                            "operation. Do not infer transitive dependencies."
                        ),
                    ),
                    LLMMessage(role="user", content=self._prompt(operation, candidates, execution)),
                ],
                temperature=0.0,
                max_tokens=self.model.max_tokens,
                response_format="json_schema",
                json_schema=OperationDependencyAnalysis.model_json_schema(),
                json_schema_name="OperationDependencyAnalysis",
                tools=[],
                tool_choice="none",
                timeout_seconds=self.model.timeout_seconds,
                metadata={"role": "operation_dependency_analyzer"},
            )
        )
        validation = self.validator.validate(response=response, output_model=OperationDependencyAnalysis)
        if not validation.valid:
            details = "; ".join(issue.message for issue in validation.errors)
            raise DependencyAnalysisError(f"Invalid dependency analysis output: {details}")
        analysis = OperationDependencyAnalysis.model_validate(validation.validated_object)
        self._validate_dependencies(operation=operation, candidates=candidates, analysis=analysis)
        return analysis

    @staticmethod
    def _prompt(
        operation: OperationReference,
        candidates: Sequence[OperationCandidate],
        execution: OperationExecutionResult,
    ) -> str:
        payload = {
            "current_operation": operation.model_dump(mode="json"),
            "candidate_operations": [candidate.model_dump(mode="json") for candidate in candidates],
            "schemathesis": {
                "outcome": execution.outcome,
                "status_code_counts": execution.status_code_counts,
                "failures": [
                    summary.model_dump(mode="json")
                    for summary in execution.failure_summaries[:20]
                ],
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _validate_dependencies(
        *,
        operation: OperationReference,
        candidates: Sequence[OperationCandidate],
        analysis: OperationDependencyAnalysis,
    ) -> None:
        allowed = {candidate.operation.identity() for candidate in candidates}
        seen: set[tuple[str, str, str | None]] = set()
        for dependency in analysis.dependencies:
            identity = dependency.identity()
            if identity == operation.identity():
                raise DependencyAnalysisError("Dependency analysis contains a self dependency")
            if identity not in allowed:
                raise DependencyAnalysisError(
                    f"Dependency analysis contains unknown operation: {dependency.method} {dependency.path}"
                )
            if identity in seen:
                raise DependencyAnalysisError(
                    f"Dependency analysis contains duplicate operation: {dependency.method} {dependency.path}"
                )
            seen.add(identity)
        if not analysis.dependency_issue and analysis.dependencies:
            raise DependencyAnalysisError("Dependencies require dependency_issue=true")
