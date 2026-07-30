"""Run curated Planner scenarios without a RESTScope database.

Each Phoenix example creates a fresh :class:`TemporaryPlanMemory`, invokes the
real ``SmokePlanAgent``, and returns the Agent result plus a compact record of
Memory calls.  This makes the experiment representative of production while
keeping one example completely isolated from every other example.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenix.evals import Score, create_evaluator
from pydantic import BaseModel, ConfigDict, Field

from evaluations.models import DatasetExample, EvaluationSuite, ScenarioProvenance
from restscope.llm import LLMClient, LLMModelConfig
from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    FailureCatalogEntry,
    FailureCandidate,
    FailureHistory,
    FailureRetrievalObservation,
    PlanMemoryWrite,
    RecordedFailure,
    RecordedPlan,
)
from restscope.operation_smoke.plan import SmokePlanAgent, SmokePlanRequest
from restscope.operation_smoke.plan.agent import _system_prompt


class PlanScenarioInput(BaseModel):
    """Supply one Batch and the complete scripted Planner memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: SmokePlanRequest
    catalog: list[FailureCatalogEntry] = Field(default_factory=list)
    histories: list[FailureHistory] = Field(default_factory=list)
    max_outputs: int = Field(default=50, ge=1, le=50)


class PlanExpectation(BaseModel):
    """Declare only the independent properties that code evaluators score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str | None = None
    case_groups: list[list[str]] | None = None
    candidate_failure_ids: list[str] | None = None
    reused_failure_ids: list[str] | None = None
    non_debuggable_case_ids: list[str] | None = None


class PlanScenario(BaseModel):
    """One isolated Planner input and its explicit deterministic expectations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^plan-[a-z0-9-]+$")
    title: str = Field(min_length=1)
    provenance: ScenarioProvenance
    tags: list[str] = Field(default_factory=list)
    input: PlanScenarioInput
    expected: PlanExpectation


def _to_example(scenario: BaseModel) -> DatasetExample:
    """Map a validated Planner scenario to Phoenix's input/reference fields."""
    item = PlanScenario.model_validate(scenario)
    return DatasetExample(
        scenario_id=item.scenario_id,
        input=item.input.model_dump(mode="json"),
        expected=item.expected.model_dump(mode="json", exclude_none=True),
        metadata={
            "title": item.title,
            "agent": "plan",
            "provenance": item.provenance.model_dump(mode="json"),
            "tags": item.tags,
        },
        splits=[*item.tags, item.scenario_id],
    )


class TemporaryPlanMemory:
    """Provide isolated, inspectable Planner memory for one Phoenix example."""

    def __init__(
        self,
        *,
        request: SmokePlanRequest,
        catalog: list[FailureCatalogEntry],
        histories: list[FailureHistory],
    ) -> None:
        """Copy scenario facts and initialize the compact call log."""
        self.request = request
        self.catalog = list(catalog)
        self.histories = {
            history.failure_id: history for history in histories
        }
        self.calls: list[dict[str, Any]] = []
        self.write: PlanMemoryWrite | None = None

    def find_failure_candidates(
        self,
        operation_key: str,
        observations: list[FailureRetrievalObservation],
    ) -> list[FailureCandidate]:
        """Return scenario-declared candidates with compact history projections.

        Scenario authors control which historical records retrieval should make
        visible. The production retrieval algorithm has separate deterministic
        tests; this Adapter isolates evaluation of Planner's semantic response
        to that candidate window.
        """
        self.calls.append(
            {
                "tool": "find_failure_candidates",
                "operation_key": operation_key,
                "case_codes": [item.case_code for item in observations],
                "failure_ids": [item.failure_id for item in self.catalog],
            }
        )
        if operation_key != self.request.operation_key:
            return []
        return [
            FailureCandidate(
                failure_id=item.failure_id,
                summary=item.summary,
                matched_case_codes=[
                    observation.case_code for observation in observations
                ],
                match_reasons=["scenario-declared-candidate"],
                observation_count=item.observation_count,
                investigation_count=item.investigation_count,
                applied_patch_count=item.applied_patch_count,
                last_seen_round=max(
                    [
                        *[
                            observation.round_number
                            for observation in self.histories.get(
                                item.failure_id,
                                FailureHistory(
                                    failure_id=item.failure_id,
                                    summary=item.summary,
                                ),
                            ).observations
                        ],
                        *[
                            investigation.round_number
                            for investigation in self.histories.get(
                                item.failure_id,
                                FailureHistory(
                                    failure_id=item.failure_id,
                                    summary=item.summary,
                                ),
                            ).investigations
                        ],
                        0,
                    ]
                ),
                recent_investigations=self.histories.get(
                    item.failure_id,
                    FailureHistory(
                        failure_id=item.failure_id,
                        summary=item.summary,
                    ),
                ).investigations[-2:],
            )
            for item in self.catalog
        ]

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Record the validated write in memory and assign deterministic IDs."""
        self.write = write
        self.calls.append(
            {
                "tool": "record_plan",
                "classification_count": len(write.classifications),
            }
        )
        known_ids = {item.failure_id for item in self.catalog}
        failures: list[RecordedFailure] = []
        new_index = 1
        for classification in write.classifications:
            failure_id = classification.failure_id
            if failure_id is None:
                while f"eval-new-{new_index}" in known_ids:
                    new_index += 1
                failure_id = f"eval-new-{new_index}"
                known_ids.add(failure_id)
                new_index += 1
            failures.append(
                RecordedFailure(
                    failure_id=failure_id,
                    summary=classification.summary,
                )
            )
        return RecordedPlan(failures=failures)


def build_task(
    *,
    client: LLMClient,
    model: LLMModelConfig,
    tracing_runtime: TracingRuntime,
    system_prompt: str | None,
    seed: int,
) -> Any:
    """Build Phoenix's Planner task around shared LLM and tracing clients.

    ``seed`` is accepted by every suite task so the experiment runner has one
    uniform Interface.  Planner has no random sampling of its own, but the
    value remains in experiment metadata and therefore is intentionally unused
    here.
    """
    del seed

    def task(input: dict[str, Any]) -> dict[str, Any]:
        """Evaluate one scenario with a new Agent and temporary Memory."""
        scenario = PlanScenarioInput.model_validate(input)
        memory = TemporaryPlanMemory(
            request=scenario.request,
            catalog=scenario.catalog,
            histories=scenario.histories,
        )
        try:
            with tracing_runtime.span(
                "evaluations.operation_smoke.plan",
                kind="CHAIN",
                input_value={
                    "operation_key": scenario.request.operation_key,
                    "round_number": scenario.request.round_number,
                },
            ) as span:
                result = SmokePlanAgent(
                    client=client,
                    model=model,
                    memory=memory,
                    system_prompt=system_prompt,
                    tracing_runtime=tracing_runtime,
                ).plan(
                    scenario.request,
                    max_outputs=scenario.max_outputs,
                )
                output = {
                    "result": result.model_dump(mode="json"),
                    "tool_calls": memory.calls,
                    "runtime_error": None,
                }
                span.set_output(output)
                return output
        except Exception as exc:  # noqa: BLE001 - runtime errors are eval data.
            return {
                "result": None,
                "tool_calls": memory.calls,
                "runtime_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            }

    return task


def _not_applicable(name: str) -> Score:
    """Return Phoenix's explicit non-scoring value for an undeclared property."""
    return Score(name=name, label="not_applicable", explanation="Not declared.")


def _binary_score(name: str, passed: bool, explanation: str) -> Score:
    """Build one consistent 0/1 code-evaluator result."""
    return Score(
        name=name,
        score=1 if passed else 0,
        label="satisfied" if passed else "not_satisfied",
        explanation=explanation,
    )


@create_evaluator(name="runtime_error", kind="code")
def runtime_error_evaluator(output: dict[str, Any]) -> Score:
    """Score whether the Agent task completed without an infrastructure error."""
    error = output.get("runtime_error")
    return _binary_score(
        "runtime_error",
        error is None,
        "Task completed." if error is None else f"Task raised {error!r}.",
    )


@create_evaluator(name="plan_status", kind="code")
def plan_status_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Compare the native Planner status when the scenario declares one."""
    wanted = expected.get("status")
    if wanted is None:
        return _not_applicable("plan_status")
    actual = (output.get("result") or {}).get("status")
    return _binary_score(
        "plan_status",
        actual == wanted,
        f"Expected status {wanted!r}; observed {actual!r}.",
    )


def _case_groups(result: dict[str, Any] | None) -> set[frozenset[str]]:
    """Normalize Todo case IDs so Agent ordering does not affect a score."""
    if not result:
        return set()
    return {
        frozenset(
            str(case.get("case_id"))
            for case in item.get("cases", [])
        )
        for item in result.get("todos", [])
    }


@create_evaluator(name="plan_case_groups", kind="code")
def plan_case_groups_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check whether independent current observations were grouped correctly."""
    wanted = expected.get("case_groups")
    if wanted is None:
        return _not_applicable("plan_case_groups")
    actual = _case_groups(output.get("result"))
    normalized_wanted = {frozenset(group) for group in wanted}
    return _binary_score(
        "plan_case_groups",
        actual == normalized_wanted,
        f"Expected groups {wanted!r}; observed {sorted(map(sorted, actual))!r}.",
    )


@create_evaluator(name="plan_candidate_retrieval", kind="code")
def plan_candidate_retrieval_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check the candidate window supplied to Planner before its model call."""
    wanted = expected.get("candidate_failure_ids")
    if wanted is None:
        return _not_applicable("plan_candidate_retrieval")
    actual = [
        failure_id
        for call in output.get("tool_calls", [])
        if call.get("tool") == "find_failure_candidates"
        for failure_id in call.get("failure_ids", [])
    ]
    return _binary_score(
        "plan_candidate_retrieval",
        set(actual) == set(wanted),
        f"Expected candidates {wanted!r}; observed {actual!r}.",
    )


@create_evaluator(name="plan_reused_failures", kind="code")
def plan_reused_failures_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check whether Planner reused stable Failure identities."""
    wanted = expected.get("reused_failure_ids")
    if wanted is None:
        return _not_applicable("plan_reused_failures")
    result = output.get("result") or {}
    actual = [
        item.get("failure_id")
        for item in [*result.get("todos", []), *result.get("non_debuggable", [])]
        if not str(item.get("failure_id", "")).startswith("eval-new-")
    ]
    return _binary_score(
        "plan_reused_failures",
        set(actual) == set(wanted),
        f"Expected reused IDs {wanted!r}; observed {actual!r}.",
    )


@create_evaluator(name="plan_nondebuggable", kind="code")
def plan_nondebuggable_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check cases explicitly declined for Solve with a reason."""
    wanted = expected.get("non_debuggable_case_ids")
    if wanted is None:
        return _not_applicable("plan_nondebuggable")
    result = output.get("result") or {}
    actual = [
        str(case.get("case_id"))
        for item in result.get("non_debuggable", [])
        if item.get("reason")
        for case in item.get("cases", [])
    ]
    return _binary_score(
        "plan_nondebuggable",
        set(actual) == set(wanted),
        f"Expected non-debuggable cases {wanted!r}; observed {actual!r}.",
    )


SUITE = EvaluationSuite(
    agent_name="plan",
    dataset_name="restscope-operation-smoke-plan",
    scenario_directory=Path(__file__).with_name("scenarios"),
    scenario_model=PlanScenario,
    to_example=_to_example,
    build_task=build_task,
    evaluators=(
        runtime_error_evaluator,
        plan_status_evaluator,
        plan_case_groups_evaluator,
        plan_candidate_retrieval_evaluator,
        plan_reused_failures_evaluator,
        plan_nondebuggable_evaluator,
    ),
    current_prompt=_system_prompt,
)
