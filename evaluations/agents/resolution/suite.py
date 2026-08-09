"""Evaluate the continuous Failure Resolution Agent without external writes.

Each Phoenix example creates a fresh Test Case Catalog, worklist, candidate
registry, and shared model-output guard. The real Resolution Agent may inspect
OpenAPI and Test Case evidence and may invoke the real Parameter Patch and
Review Agents. A small evaluation finalizer verifies candidate references and
returns identities in memory; it never opens a database transaction or sends
an HTTP request to a target API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenix.evals import Score, create_evaluator
from pydantic import BaseModel, ConfigDict, Field

from evaluations.models import DatasetExample, EvaluationSuite, ScenarioProvenance
from restscope.harness.operation_testing import (
    CatalogTestCaseDraft,
    TestCaseCatalog,
)
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.openapi_parser import OpenAPIParser
from restscope.operation_smoke.failure_resolution import (
    FailureResolutionAgent,
    FailureResolutionRequest,
    ResolutionCommit,
    ResolutionItemCommit,
    derive_failure_summary,
)
from restscope.operation_smoke.failure_resolution.prompts import (
    failure_resolution_system_prompt,
)
from restscope.operation_smoke.memory import ParameterHistory
from restscope.operation_smoke.output_limit import ModelOutputLimit
from restscope.operation_smoke.parameter_patch import ParameterPatchCoordinatorFactory
from restscope.request_generation import OperationGeneratorConfig
from restscope.tools.context import ToolContext
from restscope.tools.openapi import OpenAPIToolBackend, operation_input_references


class ResolutionScenarioInput(BaseModel):
    """Supply one failed Batch and optional executable Generator baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: FailureResolutionRequest
    openapi: dict[str, Any]
    catalog_cases: list[CatalogTestCaseDraft] = Field(min_length=1, max_length=20)
    config: OperationGeneratorConfig | None = None
    case_count: int = Field(default=5, ge=1, le=20)
    max_outputs: int = Field(default=100, ge=1, le=1_000)


class ResolutionExpectation(BaseModel):
    """Declare independently scored properties of the final Agent worklist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str | None = None
    item_count: int | None = Field(default=None, ge=0, le=100)
    source_groups: list[list[str]] | None = None
    parameter_sets: list[list[str]] | None = None
    decision_outcomes: list[str] | None = None
    applied_candidate_count: int | None = Field(default=None, ge=0, le=100)


class ResolutionScenario(BaseModel):
    """One isolated continuous Resolution session with evidence provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^resolution-[a-z0-9-]+$")
    title: str = Field(min_length=1)
    provenance: ScenarioProvenance
    tags: list[str] = Field(default_factory=list)
    input: ResolutionScenarioInput
    expected: ResolutionExpectation


class _EvaluationMemory:
    """Return an empty, correctly scoped Parameter history for Patch research."""

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Return no earlier conclusions without inventing persistent evidence."""
        del operation_key
        return ParameterHistory(input_node_id=input_node_id, failures=[])


class _EvaluationFinalizer:
    """Dereference selected candidates and return storage-free commit evidence."""

    def finalize(
        self,
        *,
        request,
        sources,
        worklist,
        candidates,
        catalog,
        current=None,
        active_constraints=None,
        prepare_patch_updates=None,
        validate_combined_patch=None,
    ) -> ResolutionCommit:
        """Model the successful persistence boundary without touching storage.

        Candidate lookup remains intentional: an ``apply_patch`` decision must
        still point at an exact reviewed object issued in this session. All
        other arguments are production-finalizer inputs that this isolated
        Agent evaluation does not reinterpret.
        """
        del (
            request,
            catalog,
            current,
            active_constraints,
            prepare_patch_updates,
            validate_combined_patch,
        )
        commits: list[ResolutionItemCommit] = []
        attempt_ids: list[str] = []
        event_ids: list[str] = []
        applied_refs: list[str] = []
        source_by_ref = {source.failure_ref: source for source in sources}
        for index, item in enumerate(worklist.items, start=1):
            if item.decision is None:
                continue
            messages = [
                source_by_ref[ref].message
                for ref in item.source_failure_refs
            ]
            failure_summary = derive_failure_summary(messages)
            attempt_id = f"eval-attempt-{index}"
            failure_id = f"eval-failure-{index}"
            attempt_ids.append(attempt_id)
            if item.decision.outcome == "no_patch":
                commits.append(
                    ResolutionItemCommit(
                        item_id=item.item_id,
                        failure_summary=failure_summary,
                        outcome="no_patch",
                        failure_id=failure_id,
                        attempt_id=attempt_id,
                    )
                )
                continue

            candidate = candidates.get(item.decision.selected_candidate_ref)
            event_id = f"eval-event-{index}"
            event_ids.append(event_id)
            applied_refs.append(candidate.candidate_ref)
            commits.append(
                ResolutionItemCommit(
                    item_id=item.item_id,
                    failure_summary=failure_summary,
                    outcome="apply_patch",
                    failure_id=failure_id,
                    attempt_id=attempt_id,
                    candidate_ref=candidate.candidate_ref,
                    generator_change_event_id=event_id,
                    patch_outputs=candidate.outputs_used,
                    changed_input_count=len(candidate.patch.updates),
                    constraint_count=len(candidate.patch.constraints),
                )
            )
        return ResolutionCommit(
            items=commits,
            attempt_ids=attempt_ids,
            generator_change_event_ids=event_ids,
            applied_candidate_refs=applied_refs,
        )


def _to_example(scenario: BaseModel) -> DatasetExample:
    """Map a validated Resolution scenario to Phoenix Dataset fields."""
    item = ResolutionScenario.model_validate(scenario)
    return DatasetExample(
        scenario_id=item.scenario_id,
        input=item.input.model_dump(mode="json"),
        expected=item.expected.model_dump(mode="json", exclude_none=True),
        metadata={
            "title": item.title,
            "agent": "resolution",
            "provenance": item.provenance.model_dump(mode="json"),
            "tags": item.tags,
        },
        splits=[*item.tags, item.scenario_id],
    )


def build_task(
    *,
    client: LLMClient,
    model: LLMModelConfig,
    task_models: dict[str, LLMModelConfig] | None = None,
    tracing_runtime: TracingRuntime,
    system_prompt: str | None,
    seed: int,
) -> Any:
    """Build Phoenix's task around the production continuous Agent loop."""

    def task(input: dict[str, Any]) -> dict[str, Any]:
        """Run one fresh session and return only JSON-safe terminal evidence."""
        scenario = ResolutionScenarioInput.model_validate(input)
        ir = OpenAPIParser.parse(scenario.openapi)
        operation = ir.operations[scenario.request.operation_key]
        context = ToolContext(ir=ir, baseline_schema_source=scenario.openapi)
        openapi_backend = OpenAPIToolBackend(context_provider=lambda: context)
        catalog = TestCaseCatalog(
            input_references=operation_input_references(operation)
        )
        for draft in scenario.catalog_cases:
            catalog.record(draft)
        try:
            configured_models = task_models or {}
            compact_model = configured_models.get(
                "operation_smoke_failure_resolution_compact",
                model.model_copy(
                    update={"role": "operation_smoke_failure_resolution_compact"}
                ),
            )
            patch_model = configured_models.get(
                "parameter_patch_agent",
                model.model_copy(update={"role": "parameter_patch_agent"}),
            )
            review_model = configured_models.get(
                "parameter_patch_review_agent",
                model.model_copy(update={"role": "parameter_patch_review_agent"}),
            )
            patch_factory = (
                ParameterPatchCoordinatorFactory(
                    client=client,
                    patch_model=patch_model,
                    review_model=review_model,
                    openapi_backend=openapi_backend,
                    tracing_runtime=tracing_runtime,
                )
                if scenario.config is not None
                else None
            )
            with tracing_runtime.span(
                "evaluations.operation_smoke.resolution",
                kind="CHAIN",
                input_value={
                    "operation_key": scenario.request.operation_key,
                    "case_count": len(scenario.request.case_ids),
                },
            ) as span:
                outcome = FailureResolutionAgent(
                    client=client,
                    model=model,
                    compact_model=compact_model,
                    openapi_backend=openapi_backend,
                    finalizer=_EvaluationFinalizer(),
                    memory=_EvaluationMemory(),
                    patch_coordinator_factory=patch_factory,
                    system_prompt=system_prompt,
                    tracing_runtime=tracing_runtime,
                ).start(
                    scenario.request,
                    catalog=catalog,
                    output_limit=ModelOutputLimit(max_outputs=scenario.max_outputs),
                    config=scenario.config,
                    case_count=scenario.case_count,
                    random_seed=seed,
                ).advance()
                output = {
                    "result": outcome.model_dump(mode="json"),
                    "runtime_error": None,
                }
                span.set_output(output)
                return output
        except Exception as exc:  # noqa: BLE001 - runtime errors are eval data.
            return {
                "result": None,
                "runtime_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }

    return task


def _not_applicable(name: str) -> Score:
    """Return an explicit N/A when a scenario omits one requirement."""
    return Score(name=name, label="not_applicable", explanation="Not declared.")


def _binary(name: str, passed: bool, explanation: str) -> Score:
    """Return the suite's consistent zero-or-one code score."""
    return Score(
        name=name,
        score=1 if passed else 0,
        label="satisfied" if passed else "not_satisfied",
        explanation=explanation,
    )


@create_evaluator(name="resolution_runtime_error", kind="code")
def runtime_error_evaluator(output: dict[str, Any]) -> Score:
    """Score task completion independently from semantic worklist quality."""
    error = output.get("runtime_error")
    return _binary(
        "resolution_runtime_error",
        error is None,
        "Task completed." if error is None else f"Task raised {error!r}.",
    )


@create_evaluator(name="resolution_status", kind="code")
def status_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Compare the terminal Resolution status when one is declared."""
    wanted = expected.get("status")
    if wanted is None:
        return _not_applicable("resolution_status")
    actual = (output.get("result") or {}).get("status")
    return _binary(
        "resolution_status",
        actual == wanted,
        f"Expected status {wanted!r}; observed {actual!r}.",
    )


def _items(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the final worklist items from a task output, if present."""
    return (((output.get("result") or {}).get("worklist") or {}).get("items") or [])


@create_evaluator(name="resolution_item_count", kind="code")
def item_count_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Compare semantic group count without interpreting group correctness."""
    wanted = expected.get("item_count")
    if wanted is None:
        return _not_applicable("resolution_item_count")
    actual = len(_items(output))
    return _binary(
        "resolution_item_count",
        actual == wanted,
        f"Expected {wanted} items; observed {actual}.",
    )


def _normalized_sets(values: list[list[str]]) -> list[tuple[str, ...]]:
    """Make unordered groups and their members stable for evaluator comparison."""
    return sorted(tuple(sorted(group)) for group in values)


@create_evaluator(name="resolution_source_groups", kind="code")
def source_groups_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check the Agent's semantic grouping of exact E references."""
    wanted = expected.get("source_groups")
    if wanted is None:
        return _not_applicable("resolution_source_groups")
    actual = [item.get("source_failure_refs", []) for item in _items(output)]
    passed = _normalized_sets(actual) == _normalized_sets(wanted)
    return _binary(
        "resolution_source_groups",
        passed,
        f"Expected groups {wanted!r}; observed {actual!r}.",
    )


@create_evaluator(name="resolution_parameters", kind="code")
def parameters_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Check semantic Parameter attribution independently from grouping."""
    wanted = expected.get("parameter_sets")
    if wanted is None:
        return _not_applicable("resolution_parameters")
    actual = [item.get("suspected_parameters", []) for item in _items(output)]
    passed = _normalized_sets(actual) == _normalized_sets(wanted)
    return _binary(
        "resolution_parameters",
        passed,
        f"Expected Parameter sets {wanted!r}; observed {actual!r}.",
    )


@create_evaluator(name="resolution_decisions", kind="code")
def decisions_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Compare declared terminal outcomes while allowing undecided items."""
    wanted = expected.get("decision_outcomes")
    if wanted is None:
        return _not_applicable("resolution_decisions")
    actual = sorted(
        item["decision"]["outcome"]
        for item in _items(output)
        if item.get("decision") is not None
    )
    return _binary(
        "resolution_decisions",
        actual == sorted(wanted),
        f"Expected decisions {wanted!r}; observed {actual!r}.",
    )


@create_evaluator(name="resolution_applied_candidates", kind="code")
def applied_candidates_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check how many exact session candidates reached the final decision."""
    wanted = expected.get("applied_candidate_count")
    if wanted is None:
        return _not_applicable("resolution_applied_candidates")
    commit = (output.get("result") or {}).get("commit") or {}
    actual = len(commit.get("applied_candidate_refs", []))
    return _binary(
        "resolution_applied_candidates",
        actual == wanted,
        f"Expected {wanted} applied candidates; observed {actual}.",
    )


SUITE = EvaluationSuite(
    agent_name="resolution",
    dataset_name="restscope-operation-smoke-resolution",
    scenario_directory=Path(__file__).with_name("scenarios"),
    scenario_model=ResolutionScenario,
    to_example=_to_example,
    build_task=build_task,
    evaluators=(
        runtime_error_evaluator,
        status_evaluator,
        item_count_evaluator,
        source_groups_evaluator,
        parameters_evaluator,
        decisions_evaluator,
        applied_candidates_evaluator,
    ),
    current_prompt=failure_resolution_system_prompt,
)
