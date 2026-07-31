"""Run curated Failure Dedup scenarios without a RESTScope database.

Every Phoenix example creates a fresh temporary Memory writer, runs the real
deterministic Deduplicator and LLM Agent, and returns native results plus a
compact write record. No scenario shares state or sends target HTTP requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenix.evals import Score, create_evaluator
from pydantic import BaseModel, ConfigDict, Field

from evaluations.models import DatasetExample, EvaluationSuite, ScenarioProvenance
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.capabilities import ToolContext, build_capabilities
from restscope.openapi_parser import OpenAPIParser
from restscope.operation_smoke.failure_dedup import (
    FailureDedupAgent,
    FailureDeduplicator,
    FailureDedupRequest,
)
from restscope.operation_smoke.failure_dedup.prompts import SYSTEM_PROMPT
from restscope.operation_smoke.memory import RecordedFailure, RecordedFailures
from restscope.operation_smoke.test_case_catalog import (
    CatalogTestCaseDraft,
    TestCaseCatalog,
)


class DedupScenarioInput(BaseModel):
    """Supply one current Batch and a bounded Agent output allowance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request: FailureDedupRequest
    valid_parameters: list[str]
    catalog_cases: list[CatalogTestCaseDraft]
    max_outputs: int = Field(default=50, ge=1, le=50)


class DedupExpectation(BaseModel):
    """Declare independently scored observable properties."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str | None = None
    failure_count: int | None = Field(default=None, ge=1)
    parameter_sets: list[list[str]] | None = None
    representative_case_ids: list[str] | None = None


class DedupScenario(BaseModel):
    """One isolated Dedup example with trace provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scenario_id: str = Field(pattern=r"^dedup-[a-z0-9-]+$")
    title: str = Field(min_length=1)
    provenance: ScenarioProvenance
    tags: list[str] = Field(default_factory=list)
    input: DedupScenarioInput
    expected: DedupExpectation


class TemporaryFailureMemory:
    """Assign stable evaluation identities and retain the validated write."""

    def __init__(self) -> None:
        self.writes = []

    def record_failures(self, write):
        """Record one successful result without database persistence."""
        self.writes.append(write)
        return RecordedFailures(
            failures=[
                RecordedFailure(
                    failure_id=f"eval-failure-{index}",
                    summary=item.summary,
                )
                for index, item in enumerate(write.failures, start=1)
            ]
        )


def _to_example(scenario: BaseModel) -> DatasetExample:
    """Map one repository scenario to Phoenix Dataset fields."""
    item = DedupScenario.model_validate(scenario)
    return DatasetExample(
        scenario_id=item.scenario_id,
        input=item.input.model_dump(mode="json"),
        expected=item.expected.model_dump(mode="json", exclude_none=True),
        metadata={
            "title": item.title,
            "agent": "dedup",
            "provenance": item.provenance.model_dump(mode="json"),
            "tags": item.tags,
        },
        splits=[*item.tags, item.scenario_id],
    )


def build_task(
    *,
    client: LLMClient,
    model: LLMModelConfig,
    tracing_runtime: TracingRuntime,
    system_prompt: str | None,
    seed: int,
) -> Any:
    """Build Phoenix's task around the production Dedup Module."""
    del seed

    def task(input: dict[str, Any]) -> dict[str, Any]:
        scenario = DedupScenarioInput.model_validate(input)
        memory = TemporaryFailureMemory()
        catalog = TestCaseCatalog(
            valid_parameters=scenario.valid_parameters
        )
        for case in scenario.catalog_cases:
            catalog.record(case)
        capability_runtime = build_capabilities(
            tracing_runtime=tracing_runtime
        )
        capability_runtime.tool_executor.bind_context(
            ToolContext(
                ir=_evaluation_ir(),
                baseline_schema_source={},
            )
        )
        try:
            result = FailureDeduplicator(
                agent=FailureDedupAgent(
                    client=client,
                    model=model,
                    tool_executor=capability_runtime.tool_executor,
                    system_prompt=system_prompt,
                    tracing_runtime=tracing_runtime,
                ),
                memory=memory,
                tracing_runtime=tracing_runtime,
            ).deduplicate(
                scenario.request,
                catalog=catalog,
                max_outputs=scenario.max_outputs,
            )
            return {
                "result": result.model_dump(mode="json"),
                "tool_calls": [
                    {"tool": "record_failures", "count": len(write.failures)}
                    for write in memory.writes
                ],
                "runtime_error": None,
            }
        except Exception as exc:  # noqa: BLE001 - runtime errors are eval data.
            return {
                "result": None,
                "tool_calls": [],
                "runtime_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }

    return task


def _evaluation_ir():
    """Build the sanitized OpenAPI contract shared by initial Dedup scenarios."""
    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Dedup evaluation", "version": "1"},
            "paths": {
                "/projects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "namespace_id": {
                                                "type": "integer"
                                            },
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"400": {"description": "bad request"}},
                    }
                }
            },
        }
    )


def _not_applicable(name: str) -> Score:
    return Score(name=name, label="not_applicable", explanation="Not declared.")


def _binary(name: str, passed: bool, explanation: str) -> Score:
    return Score(
        name=name,
        score=1 if passed else 0,
        label="satisfied" if passed else "not_satisfied",
        explanation=explanation,
    )


@create_evaluator(name="runtime_error", kind="code")
def runtime_error_evaluator(output: dict[str, Any]) -> Score:
    error = output.get("runtime_error")
    return _binary("runtime_error", error is None, f"runtime_error={error!r}")


@create_evaluator(name="dedup_status", kind="code")
def dedup_status_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Compare the Dedup terminal status when a Scenario declares one."""
    wanted = expected.get("status")
    if wanted is None:
        return _not_applicable("dedup_status")
    actual = (output.get("result") or {}).get("status")
    return _binary("dedup_status", actual == wanted, f"{actual!r} vs {wanted!r}")


@create_evaluator(name="failure_count", kind="code")
def failure_count_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Compare the number of distinct Solve work items."""
    wanted = expected.get("failure_count")
    if wanted is None:
        return _not_applicable("failure_count")
    actual = len((output.get("result") or {}).get("todos", []))
    return _binary("failure_count", actual == wanted, f"{actual} vs {wanted}")


@create_evaluator(name="parameter_sets", kind="code")
def parameter_sets_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Compare the complete suspected Parameter set for every Failure."""
    wanted_sets = expected.get("parameter_sets")
    if wanted_sets is None:
        return _not_applicable("parameter_sets")
    todos = (output.get("result") or {}).get("todos", [])
    actual = sorted(sorted(item.get("suspected_parameters") or []) for item in todos)
    wanted = sorted(sorted(item) for item in wanted_sets)
    return _binary("parameter_sets", actual == wanted, f"{actual!r} vs {wanted!r}")


@create_evaluator(name="representative_cases", kind="code")
def representative_cases_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check that Dedup retained the intended first-seen representative cases."""
    wanted = expected.get("representative_case_ids")
    if wanted is None:
        return _not_applicable("representative_cases")
    todos = (output.get("result") or {}).get("todos", [])
    actual = sorted(
        str(item.get("test_case_id"))
        for item in todos
    )
    return _binary(
        "representative_cases",
        actual == sorted(wanted),
        f"{actual!r} vs {sorted(wanted)!r}",
    )


SUITE = EvaluationSuite(
    agent_name="dedup",
    dataset_name="restscope-operation-smoke-dedup",
    scenario_directory=Path(__file__).with_name("scenarios"),
    scenario_model=DedupScenario,
    to_example=_to_example,
    build_task=build_task,
    evaluators=(
        runtime_error_evaluator,
        dedup_status_evaluator,
        failure_count_evaluator,
        parameter_sets_evaluator,
        representative_cases_evaluator,
    ),
    current_prompt=lambda: SYSTEM_PROMPT,
)
