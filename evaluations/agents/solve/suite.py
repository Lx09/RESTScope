"""Evaluate Failure Solve with scripted tools and no external side effects.

The real ``FailureSolveAgent`` still decides whether to inspect Parameter
history, probe the current operation, request a Patch, and finish with
``apply_patch`` or ``no_patch``. Runtime may still report a conflict when an
accepted candidate cannot commit. Only the tool implementations are replaced:
they replay structured scenario evidence, record calls, and keep accepted
Generator state in memory rather than a RESTScope database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phoenix.evals import Score, create_evaluator
from pydantic import BaseModel, ConfigDict, Field

from evaluations.models import DatasetExample, EvaluationSuite, ScenarioProvenance
from restscope.capabilities import OpenAPICapability, ToolContext
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.observability import TracingRuntime
from restscope.openapi_parser import OpenAPIParser
from restscope.operation_smoke.failure_solver import (
    FailureSolveAgent,
    FailureSolveRequest,
)
from restscope.operation_smoke.failure_solver.agent import _system_prompt
from restscope.operation_smoke.memory import (
    AppliedSmokePatch,
    FailureHistory,
    ParameterHistory,
    SolveAttemptWrite,
)
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    ParameterPatchFailure,
    ValidatedParameterPatch,
)
from restscope.operation_smoke.test_case_catalog import (
    CatalogTestCaseDraft,
    TestCaseCatalog,
    parse_http_failure,
    parse_transport_failure,
)
from restscope.testing import (
    OperationGeneratorConfig,
    SchemaSnapshot,
    build_semantic_input_map,
    preview_generator_patch,
)


class SolveScenarioInput(BaseModel):
    """Supply one Solve session and every possible scripted tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: FailureSolveRequest
    config: OperationGeneratorConfig
    failure_history: FailureHistory
    catalog_cases: list[CatalogTestCaseDraft]
    parameter_histories: list[ParameterHistory] = Field(default_factory=list)
    probe_results: list[ToolResult] = Field(default_factory=list)
    patch_results: list[
        ValidatedParameterPatch | ParameterPatchFailure
    ] = Field(default_factory=list)
    active_constraints: list[CompiledConstraintPatch] = Field(default_factory=list)
    case_count: int = Field(default=3, ge=1, le=20)
    max_outputs: int = Field(default=50, ge=1, le=50)
    max_patch_outputs: int = Field(default=20, ge=1, le=20)


class SolveExpectation(BaseModel):
    """Declare independent Solve properties for Phoenix code evaluators."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str | None = None
    memory_input_handles: list[str] | None = None
    minimum_probe_calls: int | None = Field(default=None, ge=0)
    minimum_patch_calls: int | None = Field(default=None, ge=0)
    applied_patch_count: int | None = Field(default=None, ge=0)


class SolveScenario(BaseModel):
    """One isolated Solve session with scripted non-network collaborators."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(pattern=r"^solve-[a-z0-9-]+$")
    title: str = Field(min_length=1)
    provenance: ScenarioProvenance
    tags: list[str] = Field(default_factory=list)
    input: SolveScenarioInput
    expected: SolveExpectation


def _openapi_capability(config: OperationGeneratorConfig) -> OpenAPICapability:
    """Build the trusted OpenAPI context available to an offline Solve eval.

    Evaluation scenarios retain the exact request snapshot but intentionally
    do not carry response contracts.  The Adapter rebuilds the declared inputs
    so a real evaluation model can use the two request lookup tools; it leaves
    responses content-free rather than inventing response field Schemas.
    """
    snapshot = config.snapshot
    nodes_by_id = {node.input_node_id: node for node in snapshot.input_nodes}
    parameters = []
    for parameter in snapshot.parameters:
        node = nodes_by_id[parameter.input_node_id]
        parameters.append(
            {
                "name": parameter.name,
                "in": parameter.location,
                "required": parameter.required,
                "style": parameter.style,
                "explode": parameter.explode,
                "allowReserved": parameter.allow_reserved,
                "schema": _schema_source(node.schema_contract),
            }
        )
    contents = {
        media_type: {
            "schema": _schema_source(nodes_by_id[node_id].schema_contract)
        }
        for media_type, node_id in snapshot.media_type_node_ids.items()
        if nodes_by_id[node_id].schema_contract is not None
    }
    request_body_node = (
        nodes_by_id.get(snapshot.request_body_node_id)
        if snapshot.request_body_node_id is not None
        else None
    )
    operation_source: dict[str, Any] = {
        "parameters": parameters,
        "responses": {"200": {"description": "evaluation"}},
    }
    if contents:
        operation_source["requestBody"] = {
            "required": bool(request_body_node and request_body_node.required),
            "content": contents,
        }
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Solve evaluation", "version": "1"},
            "paths": {
                snapshot.path: {
                    snapshot.method.casefold(): operation_source
                }
            },
        }
    )
    context = ToolContext(ir=ir, baseline_schema_source={})
    return OpenAPICapability(context_provider=lambda: context)


def _schema_source(schema: SchemaSnapshot | None) -> dict[str, Any]:
    """Convert one frozen request Schema back to parser-readable OpenAPI data."""
    if schema is None:
        return {}
    output: dict[str, Any] = {
        target: value
        for target, value in (
            ("type", schema.type),
            ("format", schema.format),
            ("enum", schema.enum),
            ("nullable", schema.nullable),
            ("minimum", schema.minimum),
            ("maximum", schema.maximum),
            ("exclusiveMinimum", schema.exclusive_minimum),
            ("exclusiveMaximum", schema.exclusive_maximum),
            ("minLength", schema.min_length),
            ("maxLength", schema.max_length),
            ("pattern", schema.pattern),
            ("minItems", schema.min_items),
            ("maxItems", schema.max_items),
            ("uniqueItems", schema.unique_items),
            ("minProperties", schema.min_properties),
            ("maxProperties", schema.max_properties),
        )
        if value is not None
    }
    if schema.has_const:
        output["const"] = schema.const
    if schema.required:
        output["required"] = list(schema.required)
    if schema.properties:
        output["properties"] = {
            name: {
                **_schema_source(child),
                **(
                    {"readOnly": True}
                    if name in schema.read_only_properties
                    else {}
                ),
            }
            for name, child in schema.properties.items()
        }
    if schema.items is not None:
        output["items"] = _schema_source(schema.items)
    if isinstance(schema.additional_properties, SchemaSnapshot):
        output["additionalProperties"] = _schema_source(
            schema.additional_properties
        )
    elif schema.additional_properties is not None:
        output["additionalProperties"] = schema.additional_properties
    for target, branches in (
        ("allOf", schema.all_of),
        ("anyOf", schema.any_of),
        ("oneOf", schema.one_of),
    ):
        if branches:
            output[target] = [_schema_source(branch) for branch in branches]
    return output


def _to_example(scenario: BaseModel) -> DatasetExample:
    """Map a validated Solve scenario to a Phoenix Dataset example."""
    item = SolveScenario.model_validate(scenario)
    return DatasetExample(
        scenario_id=item.scenario_id,
        input=item.input.model_dump(mode="json"),
        expected=item.expected.model_dump(mode="json", exclude_none=True),
        metadata={
            "title": item.title,
            "agent": "solve",
            "provenance": item.provenance.model_dump(mode="json"),
            "tags": item.tags,
        },
        splits=[*item.tags, item.scenario_id],
    )


class TemporarySolveMemory:
    """Replay current Failure and Parameter history for one Solve session."""

    def __init__(self, scenario: SolveScenarioInput, calls: list[dict[str, Any]]):
        """Index scenario histories and share the task's compact call log."""
        self.scenario = scenario
        self.calls = calls
        self.parameter_by_node = {
            item.input_node_id: item for item in scenario.parameter_histories
        }

    def failure_history(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> FailureHistory:
        """Return the one current Failure history loaded at session start."""
        self.calls.append(
            {
                "tool": "failure_history",
                "operation_key": operation_key,
                "failure_id": failure_id,
            }
        )
        if failure_id != self.scenario.failure_history.failure_id:
            raise KeyError(f"Unknown scripted Failure: {failure_id}")
        return self.scenario.failure_history

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Return one structured value for a requested input node."""
        self.calls.append(
            {
                "tool": "lookup_parameter_history",
                "operation_key": operation_key,
                "input_node_ids": [input_node_id],
                "unconfigured": (
                    []
                    if input_node_id in self.parameter_by_node
                    else [input_node_id]
                ),
            }
        )
        return self.parameter_by_node.get(
            input_node_id,
            ParameterHistory(input_node_id=input_node_id),
        )

    def record_solve_attempt(self, write: SolveAttemptWrite) -> str:
        """Retain no-Patch or conflict facts in task output rather than a DB."""
        self.calls.append(
            {
                "tool": "record_solve_attempt",
                "outcome": write.outcome,
                "parameters": [
                    item.input_node_id for item in write.parameters
                ],
            }
        )
        return "eval-solve-attempt"


class ScriptedHTTPProbe:
    """Offer a scoped HTTP-shaped tool that never opens a network connection."""

    def __init__(
        self,
        results: list[ToolResult],
        calls: list[dict[str, Any]],
    ) -> None:
        """Copy configured results so each task repetition starts fresh."""
        self.results = list(results)
        self.calls = calls

    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec:
        """Describe a small request shape limited to the scenario operation."""
        return ToolSpec(
            name="restscope.http.request",
            description=(
                "Probe the current operation only. Evaluation replays a "
                "sanitized structured response and sends no network request."
            ),
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "path": {"type": "string"},
                    "path_parameters": {"type": "object"},
                    "query": {"type": "object"},
                    "body": {},
                },
                "required": ["method", "path"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "case_id": {"type": "string", "pattern": "^TC[1-9][0-9]*$"},
                    "status_code": {"type": "integer"},
                    "failure": {},
                },
                "required": ["case_id", "status_code", "failure"],
                "additionalProperties": False,
            },
            read_only=True,
        )

    def validate(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> str | None:
        """Reject unknown tools and attempts to leave the current operation."""
        if tool_call.name != "restscope.http.request":
            return f"Unknown Solve tool: {tool_call.name}"
        method = tool_call.arguments.get("method")
        path = tool_call.arguments.get("path")
        if method != config.snapshot.method or path != config.snapshot.path:
            return (
                "The evaluation HTTP probe is restricted to "
                f"{config.snapshot.method} {config.snapshot.path}."
            )
        return None

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
        catalog: TestCaseCatalog,
    ) -> ToolResult:
        """Replay one configured result or return a structured missing-script error."""
        del config
        self.calls.append(
            {
                "tool": "restscope.http.request",
                "arguments": tool_call.arguments,
            }
        )
        if not self.results:
            scripted = ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="failed",
                error={
                    "code": "scenario_probe_not_configured",
                    "message": "This scenario did not configure another probe.",
                },
            )
        else:
            scripted = self.results.pop(0).model_copy(
                update={
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                }
            )
        structured = (
            scripted.structured
            if isinstance(scripted.structured, dict)
            else {}
        )
        status_code = structured.get("status_code")
        parameters = {
            f"path.{name}": value
            for name, value in (
                tool_call.arguments.get("path_parameters") or {}
            ).items()
        }
        if scripted.status == "succeeded" and isinstance(status_code, int):
            body = structured.get("body") if 400 <= status_code < 600 else None
            failure = parse_http_failure(
                status_code=status_code,
                reason_phrase=str(structured.get("reason_phrase") or ""),
                media_type=str(structured.get("media_type") or ""),
                response_body=body,
                body_truncated=False,
            )
        else:
            body = None
            error = scripted.error or {}
            failure = parse_transport_failure(
                code=str(error.get("code") or scripted.status),
                message=str(error.get("message") or "scripted probe failed"),
            )
        case = catalog.record(
            CatalogTestCaseDraft(
                parameters=parameters,
                response_body=body,
                failure=failure,
            )
        )
        return scripted.model_copy(
            update={
                "tool_call_id": tool_call.id,
                "name": tool_call.name,
                "structured": {
                    "case_id": case.case_id,
                    **(
                        {"status_code": status_code}
                        if isinstance(status_code, int)
                        else {}
                    ),
                    "failure": (
                        failure.model_dump(mode="json")
                        if failure is not None
                        else None
                    ),
                },
            }
        )


class ScriptedPatchCoordinator:
    """Return one prepared reviewed Patch while recording Solve's requirement."""

    def __init__(
        self,
        result: ValidatedParameterPatch | ParameterPatchFailure,
        calls: list[dict[str, Any]],
    ) -> None:
        """Bind exactly one result to one fresh nested Coordinator instance."""
        self.result = result
        self.calls = calls

    def run(self, **kwargs: Any) -> ValidatedParameterPatch | ParameterPatchFailure:
        """Record the structured requirement and return sanitized Patch evidence."""
        task = kwargs["task"]
        self.calls.append(
            {
                "tool": "generate_parameter_patch",
                "affected_inputs": list(task.affected_inputs),
                "root_cause": task.root_cause,
                "prior_attempt_count": len(task.prior_attempts),
            }
        )
        return self.result


class ScriptedPatchCoordinatorFactory:
    """Create one side-effect-free scripted Coordinator per tool call."""

    def __init__(
        self,
        results: list[ValidatedParameterPatch | ParameterPatchFailure],
        calls: list[dict[str, Any]],
    ) -> None:
        """Copy prepared results for repetition isolation."""
        self.results = list(results)
        self.calls = calls

    def create(self) -> ScriptedPatchCoordinator:
        """Return a prepared result, or a structured budget-style tool failure."""
        if self.results:
            result = self.results.pop(0)
        else:
            result = ParameterPatchFailure(
                todo_id="unconfigured",
                reason="output_budget_exhausted",
                outputs_used=1,
                errors=["This scenario did not configure another Patch result."],
            )
        return ScriptedPatchCoordinator(result, self.calls)


class TemporaryPatchApplication:
    """Apply an accepted candidate to in-memory current Generator state."""

    def __init__(
        self,
        config: OperationGeneratorConfig,
        calls: list[dict[str, Any]],
    ) -> None:
        """Keep task-local state and the shared compact call log."""
        self.config = config
        self.calls = calls

    def apply(self, **kwargs: Any) -> AppliedSmokePatch:
        """Preview chosen updates and expose current-state audit identities."""
        patch = kwargs["patch"]
        self.config = preview_generator_patch(self.config, patch.updates)
        self.calls.append(
            {
                "tool": "apply_patch",
                "update_count": len(patch.updates),
                "constraint_count": len(patch.constraints),
            }
        )
        return AppliedSmokePatch(
            config=self.config,
            solve_attempt_id="eval-solve-attempt-applied",
            generator_change_event_id="eval-generator-change-applied",
            constraints=(),
        )


def build_task(
    *,
    client: LLMClient,
    model: LLMModelConfig,
    tracing_runtime: TracingRuntime,
    system_prompt: str | None,
    seed: int,
) -> Any:
    """Build Phoenix's Solve task using real Agent logic and scripted tools."""

    def task(input: dict[str, Any]) -> dict[str, Any]:
        """Evaluate one Solve session with fresh collaborators and call logs."""
        scenario = SolveScenarioInput.model_validate(input)
        # Production Coordinator supplies both the live operation view and the
        # complete frozen Generator config. YAML keeps those facts in one
        # canonical ``config`` object, so the Adapter expands the real request
        # shape here instead of duplicating a large snapshot in every Scenario.
        request = scenario.request.model_copy(
            update={
                "generator_config": scenario.config.model_dump(mode="json"),
                "operation": {
                    **scenario.request.operation,
                    "testing_snapshot": scenario.config.snapshot.model_dump(
                        mode="json"
                    ),
                },
            }
        )
        calls: list[dict[str, Any]] = []
        memory = TemporarySolveMemory(scenario, calls)
        semantic = build_semantic_input_map(scenario.config)
        catalog = TestCaseCatalog(
            input_references=semantic.reference_by_handle.values(),
        )
        for case in scenario.catalog_cases:
            catalog.record(case)
        try:
            with tracing_runtime.span(
                "evaluations.operation_smoke.solve",
                kind="CHAIN",
                input_value={
                    "operation_key": scenario.request.operation_key,
                    "todo_id": scenario.request.todo.todo_id,
                },
            ) as span:
                outcome = FailureSolveAgent(
                    client=client,
                    model=model,
                    http_probe=ScriptedHTTPProbe(scenario.probe_results, calls),
                    memory=memory,
                    patch_coordinator_factory=ScriptedPatchCoordinatorFactory(
                        scenario.patch_results,
                        calls,
                    ),
                    patch_application=TemporaryPatchApplication(
                        scenario.config,
                        calls,
                    ),
                    openapi_capability=_openapi_capability(scenario.config),
                    system_prompt=system_prompt,
                    tracing_runtime=tracing_runtime,
                ).start(
                    request,
                    catalog=catalog,
                    config=scenario.config,
                    active_constraints=scenario.active_constraints,
                    case_count=scenario.case_count,
                    random_seed=seed,
                    max_patch_outputs=scenario.max_patch_outputs,
                    max_outputs=scenario.max_outputs,
                ).advance()
                output = {
                    "result": outcome.model_dump(mode="json"),
                    "tool_calls": calls,
                    "runtime_error": None,
                }
                span.set_output(output)
                return output
        except Exception as exc:  # noqa: BLE001 - runtime errors are eval data.
            return {
                "result": None,
                "tool_calls": calls,
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


@create_evaluator(name="solve_runtime_error", kind="code")
def runtime_error_evaluator(output: dict[str, Any]) -> Score:
    """Score task completion separately from semantic Agent quality."""
    error = output.get("runtime_error")
    return _score(
        "solve_runtime_error",
        error is None,
        "Task completed." if error is None else f"Task raised {error!r}.",
    )


@create_evaluator(name="solve_status", kind="code")
def status_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Compare the terminal native Solve status."""
    wanted = expected.get("status")
    if wanted is None:
        return _na("solve_status")
    actual = (output.get("result") or {}).get("status")
    return _score(
        "solve_status",
        actual == wanted,
        f"Expected status {wanted!r}; observed {actual!r}.",
    )


def _minimum_tool_score(
    *,
    name: str,
    output: dict[str, Any],
    expected: dict[str, Any],
    expected_key: str,
    tool_name: str,
) -> Score:
    """Count a named tool without depending on model call ordering."""
    minimum = expected.get(expected_key)
    if minimum is None:
        return _na(name)
    actual = sum(
        call.get("tool") == tool_name
        for call in output.get("tool_calls", [])
    )
    return _score(
        name,
        actual >= minimum,
        f"Expected at least {minimum} calls; observed {actual}.",
    )


@create_evaluator(name="solve_probe_calls", kind="code")
def probe_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Check whether Solve gathered the requested scripted HTTP evidence."""
    return _minimum_tool_score(
        name="solve_probe_calls",
        output=output,
        expected=expected,
        expected_key="minimum_probe_calls",
        tool_name="restscope.http.request",
    )


@create_evaluator(name="solve_patch_calls", kind="code")
def patch_evaluator(output: dict[str, Any], expected: dict[str, Any]) -> Score:
    """Check whether Solve requested a structured Patch candidate."""
    return _minimum_tool_score(
        name="solve_patch_calls",
        output=output,
        expected=expected,
        expected_key="minimum_patch_calls",
        tool_name="generate_parameter_patch",
    )


@create_evaluator(name="solve_applied_patch_count", kind="code")
def application_evaluator(
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check exactly how many candidates caused an in-memory state change."""
    wanted = expected.get("applied_patch_count")
    if wanted is None:
        return _na("solve_applied_patch_count")
    actual = sum(
        call.get("tool") == "apply_patch"
        for call in output.get("tool_calls", [])
    )
    return _score(
        "solve_applied_patch_count",
        actual == wanted,
        f"Expected {wanted} applications; observed {actual}.",
    )


@create_evaluator(name="solve_memory_inputs", kind="code")
def memory_evaluator(
    input: dict[str, Any],
    output: dict[str, Any],
    expected: dict[str, Any],
) -> Score:
    """Check node IDs resolved from the semantic handles chosen by Solve."""
    wanted = expected.get("memory_input_handles")
    if wanted is None:
        return _na("solve_memory_inputs")
    scenario = SolveScenarioInput.model_validate(input)
    semantic = build_semantic_input_map(scenario.config)
    wanted_nodes = {
        semantic.node_by_handle[handle]
        for handle in wanted
        if handle in semantic.node_by_handle
    }
    actual = {
        node_id
        for call in output.get("tool_calls", [])
        if call.get("tool") == "lookup_parameter_history"
        for node_id in call.get("input_node_ids", [])
    }
    return _score(
        "solve_memory_inputs",
        actual == wanted_nodes,
        f"Expected nodes {sorted(wanted_nodes)!r}; observed {sorted(actual)!r}.",
    )


SUITE = EvaluationSuite(
    agent_name="solve",
    dataset_name="restscope-operation-smoke-solve",
    scenario_directory=Path(__file__).with_name("scenarios"),
    scenario_model=SolveScenario,
    to_example=_to_example,
    build_task=build_task,
    evaluators=(
        runtime_error_evaluator,
        status_evaluator,
        memory_evaluator,
        probe_evaluator,
        patch_evaluator,
        application_evaluator,
    ),
    current_prompt=_system_prompt,
)
