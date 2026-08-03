"""Evaluate Parameter Patch through its real Coordinator and local checks.

Unlike Solve, this suite does not script the Agent result.  The real
``ParameterPatchCoordinator`` asks one Patch Agent for a proposal, compiles
semantic handles into stable nodes, generates deterministic samples, and asks
a fresh Review Agent for its verdict. No database or target HTTP transport is
imported by this Module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenix.evals import Score, create_evaluator
from pydantic import BaseModel, ConfigDict, Field

from evaluations.models import DatasetExample, EvaluationSuite, ScenarioProvenance
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    ParameterPatchCoordinator,
    ParameterPatchTask,
)
from restscope.operation_smoke.parameter_patch.prompts import EXPERT_SYSTEM_PROMPT
from restscope.testing import OperationGeneratorConfig, build_semantic_input_map


class PatchScenarioInput(BaseModel):
    """Supply one Generator Requirement and executable operation snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: ParameterPatchTask
    config: OperationGeneratorConfig
    active_constraints: list[CompiledConstraintPatch] = Field(default_factory=list)
    case_count: int = Field(default=5, ge=1, le=20)
    max_outputs: int = Field(default=20, ge=1, le=20)


class GeneratorRequirement(BaseModel):
    """Describe the expected Generator strategy by semantic input handle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_handle: str = Field(min_length=1)
    strategy_type: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None


class PatchExpectation(BaseModel):
    """Declare independently scored properties of a validated candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str | None = None
    generators: list[GeneratorRequirement] | None = None
    minimum_constraint_count: int | None = Field(default=None, ge=0)
    constraint_input_handles: list[str] | None = None


class PatchScenario(BaseModel):
    """One executable Generator Requirement and deterministic expectations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^patch-[a-z0-9-]+$")
    title: str = Field(min_length=1)
    provenance: ScenarioProvenance
    tags: list[str] = Field(default_factory=list)
    input: PatchScenarioInput
    expected: PatchExpectation


def _to_example(scenario: BaseModel) -> DatasetExample:
    """Map a validated Patch scenario to a Phoenix Dataset example."""
    item = PatchScenario.model_validate(scenario)
    return DatasetExample(
        scenario_id=item.scenario_id,
        input=item.input.model_dump(mode="json"),
        expected=item.expected.model_dump(mode="json", exclude_none=True),
        metadata={
            "title": item.title,
            "agent": "patch",
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
    """Build Phoenix's Patch task around production validation behavior."""

    def task(input: dict[str, Any]) -> dict[str, Any]:
        """Run one fresh Patch/Review coordination and return JSON-safe evidence."""
        scenario = PatchScenarioInput.model_validate(input)
        semantic = build_semantic_input_map(scenario.config)
        try:
            with tracing_runtime.span(
                "evaluations.operation_smoke.patch",
                kind="CHAIN",
                input_value={
                    "operation_key": scenario.config.operation_key,
                    "todo_id": scenario.task.todo_id,
                },
            ) as span:
                result = ParameterPatchCoordinator(
                    client=client,
                    patch_model=model,
                    review_model=model.model_copy(
                        update={"role": "parameter_patch_review_agent"}
                    ),
                    patch_system_prompt=system_prompt,
                    tracing_runtime=tracing_runtime,
                ).run(
                    task=scenario.task,
                    config=scenario.config,
                    active_constraints=scenario.active_constraints,
                    case_count=scenario.case_count,
                    random_seed=seed,
                    max_outputs=scenario.max_outputs,
                )
                output = {
                    "result": result.model_dump(mode="json"),
                    "tool_calls": [],
                    # This storage-free map lets evaluators and humans read a
                    # Patch using the same handles shown to the model.
                    "input_handle_by_node": dict(semantic.handle_by_node),
                    "runtime_error": None,
                }
                span.set_output(output)
                return output
        except Exception as exc:  # noqa: BLE001 - runtime errors are eval data.
            return {
                "result": None,
                "tool_calls": [],
                "input_handle_by_node": dict(semantic.handle_by_node),
                "runtime_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }

    return task


def _na(name: str) -> Score:
    """Return an explicit non-scoring result for an undeclared requirement."""
    return Score(name=name, label="not_applicable", explanation="Not declared.")


def _score(name: str, passed: bool, explanation: str) -> Score:
    """Return the suite's consistent 0/1 code score."""
    return Score(
        name=name,
        score=1 if passed else 0,
        label="satisfied" if passed else "not_satisfied",
        explanation=explanation,
    )


@create_evaluator(name="patch_runtime_error", kind="code")
def runtime_error_evaluator(output: dict[str, Any]) -> Score:
    """Score task completion separately from semantic Patch quality."""
    error = output.get("runtime_error")
    return _score(
        "patch_runtime_error",
        error is None,
        "Task completed." if error is None else f"Task raised {error!r}.",
    )


@create_evaluator(name="patch_status", kind="code")
def status_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Compare the native Patch status when declared by the scenario."""
    wanted = expected.get("status")
    if wanted is None:
        return _na("patch_status")
    actual = (output.get("result") or {}).get("status")
    return _score(
        "patch_status",
        actual == wanted,
        f"Expected status {wanted!r}; observed {actual!r}.",
    )


def _updates_by_handle(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Project compiled node-ID updates back to semantic prompt handles."""
    result = output.get("result") or {}
    patch = result.get("patch") or {}
    handle_by_node = output.get("input_handle_by_node", {})
    return {
        handle_by_node.get(update.get("input_node_id"), "<unknown>"): update
        for update in patch.get("updates", [])
    }


@create_evaluator(name="patch_generators", kind="code")
def generators_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check each declared strategy type and inclusive numeric bound."""
    requirements = expected.get("generators")
    if requirements is None:
        return _na("patch_generators")
    updates = _updates_by_handle(output)
    mismatches: list[str] = []
    for requirement in requirements:
        handle = requirement["input_handle"]
        strategy = (updates.get(handle) or {}).get("strategy") or {}
        for expected_key, strategy_key in (
            ("strategy_type", "type"),
            ("minimum", "minimum"),
            ("maximum", "maximum"),
        ):
            wanted = requirement.get(expected_key)
            if wanted is not None and strategy.get(strategy_key) != wanted:
                mismatches.append(
                    f"{handle}.{strategy_key}: expected {wanted!r}, "
                    f"observed {strategy.get(strategy_key)!r}"
                )
    return _score(
        "patch_generators",
        not mismatches,
        "All Generator requirements matched."
        if not mismatches
        else "; ".join(mismatches),
    )


@create_evaluator(name="patch_constraint_count", kind="code")
def constraint_count_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check the minimum number of compiled cross-Parameter Constraints."""
    minimum = expected.get("minimum_constraint_count")
    if minimum is None:
        return _na("patch_constraint_count")
    result = output.get("result") or {}
    actual = len((result.get("patch") or {}).get("constraints", []))
    return _score(
        "patch_constraint_count",
        actual >= minimum,
        f"Expected at least {minimum} Constraints; observed {actual}.",
    )


def _constraint_node_ids(value: Any) -> set[str]:
    """Collect stable input nodes from a recursively compiled expression."""
    if isinstance(value, list):
        return set().union(*(_constraint_node_ids(item) for item in value), set())
    if not isinstance(value, dict):
        return set()
    found = (
        {value["input_node_id"]}
        if isinstance(value.get("input_node_id"), str)
        else set()
    )
    for child in value.values():
        found.update(_constraint_node_ids(child))
    return found


@create_evaluator(name="patch_constraint_inputs", kind="code")
def constraint_inputs_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check that a compiled relationship covers every required Parameter."""
    wanted = expected.get("constraint_input_handles")
    if wanted is None:
        return _na("patch_constraint_inputs")
    result = output.get("result") or {}
    constraints = (result.get("patch") or {}).get("constraints", [])
    nodes = _constraint_node_ids(constraints)
    handle_by_node = output.get("input_handle_by_node", {})
    actual = {handle_by_node.get(node, "<unknown>") for node in nodes}
    return _score(
        "patch_constraint_inputs",
        set(wanted).issubset(actual),
        f"Expected handles {wanted!r}; observed {sorted(actual)!r}.",
    )


SUITE = EvaluationSuite(
    agent_name="patch",
    dataset_name="restscope-operation-smoke-patch",
    scenario_directory=Path(__file__).with_name("scenarios"),
    scenario_model=PatchScenario,
    to_example=_to_example,
    build_task=build_task,
    evaluators=(
        runtime_error_evaluator,
        status_evaluator,
        generators_evaluator,
        constraint_count_evaluator,
        constraint_inputs_evaluator,
    ),
    current_prompt=lambda: EXPERT_SYSTEM_PROMPT,
)
