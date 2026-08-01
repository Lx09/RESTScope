"""Solve one Failure and finish with one durable terminal decision.

Failure Solve is the Agent that decides which Parameters caused a Failure and
whether a Generator Patch should be applied. It can read Parameter history,
probe the exact current operation, and run a fresh Parameter Patch Agent. Patch
candidates remain session-local and have no
side effects until the model returns ``apply_patch`` with a valid candidate
reference.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from restscope.capabilities import (
    AgentToolbox,
    HTTP_REQUEST_TOOL_NAME,
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OpenAPICapability,
    ToolFailure,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
)
from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    AppliedSmokePatch,
    FailureHistory,
    ParameterHistory,
    PatchSolveAttempt,
    SolveAttemptParameterWrite,
    SolveAttemptWrite,
)
from restscope.operation_smoke.parameter_patch import (
    AvailableReferenceOption,
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    ParameterPatchAgent,
    ParameterPatchFailure,
    ParameterPatchTask,
    ValidatedParameterPatch,
)
from restscope.operation_smoke.test_case_catalog import (
    CATALOG_QUERY_TOOL_NAME,
    TestCaseCatalog,
    catalog_query_tool_spec,
    query_catalog,
    tool_result_json,
)
from restscope.testing import (
    OperationGeneratorConfig,
    ReferenceValueProvider,
    build_semantic_input_map,
    preview_generator_patch,
)
from restscope.testing.ports import GeneratorConfigConcurrentWrite

from .schemas import (
    FailureSolveDecision,
    FailureSolveOutcome,
    FailureSolveRequest,
    PatchCandidate,
)


_MEMORY_TOOL_NAME = "lookup_parameter_history"
_PATCH_TOOL_NAME = "generate_parameter_patch"
_READ_ONLY_QUERY_TOOL_NAMES = {
    _MEMORY_TOOL_NAME,
    CATALOG_QUERY_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
}


class HTTPProbe(Protocol):
    """Describe the HTTP capability restricted to the current operation."""

    def tool_spec(self, config: OperationGeneratorConfig) -> ToolSpec:
        """Return the HTTP tool description restricted to the current operation."""
        ...

    def validate(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
    ) -> str | None:
        """Return a scope error without sending the requested HTTP call."""
        ...

    def execute(
        self,
        *,
        config: OperationGeneratorConfig,
        tool_call: ToolCall,
        catalog: TestCaseCatalog,
    ) -> ToolResult:
        """Execute and record a previously scope-checked diagnostic request."""
        ...


class SolveMemory(Protocol):
    """Describe the memory reads and non-Patch write owned by Solve runtime."""

    def failure_history(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> FailureHistory:
        """Load the complete history of one operation-scoped Failure."""
        ...

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Load past causes and repairs linked to one operation input."""
        ...

    def record_solve_attempt(self, write: SolveAttemptWrite) -> str:
        """Append a validated no-Patch or conflict Solve Attempt."""
        ...


class PatchAgentFactory(Protocol):
    """Create a fresh Patch Agent for each tool invocation."""

    def create(self) -> ParameterPatchAgent:
        """Return a fresh Agent with an empty proposal conversation."""
        ...


class PatchApplication(Protocol):
    """Atomically change current test state and record an applied Solve Attempt."""

    def apply(
        self,
        *,
        current: OperationGeneratorConfig,
        expected_constraints: list[CompiledConstraintPatch],
        patch: GeneratorPatchDraft,
        attempt: PatchSolveAttempt,
    ) -> AppliedSmokePatch:
        """Commit a candidate only if its Generator and Constraint view is current."""
        ...


class FailureSolveAgent:
    """Create one isolated LLM Solve session for one deduplicated Failure."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        memory: SolveMemory,
        patch_agent_factory: PatchAgentFactory,
        patch_application: PatchApplication,
        openapi_capability: OpenAPICapability,
        reference_values: ReferenceValueProvider | None = None,
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store shared stateless collaborators; sessions own mutable state.

        ``system_prompt`` is a complete-prompt evaluation seam. Normal App
        construction leaves it unset and preserves the built-in instructions.
        """
        self.client = client
        self.model = model
        self.http_probe = http_probe
        self.memory = memory
        self.patch_agent_factory = patch_agent_factory
        self.patch_application = patch_application
        self.openapi_capability = openapi_capability
        self.reference_values = reference_values
        self.system_prompt = system_prompt or _system_prompt()
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def start(
        self,
        request: FailureSolveRequest,
        *,
        catalog: TestCaseCatalog,
        config: OperationGeneratorConfig,
        active_constraints: list[CompiledConstraintPatch],
        case_count: int,
        random_seed: int,
        max_patch_outputs: int = 20,
        prepare_patch_updates: Callable | None = None,
        max_outputs: int = 50,
        continuation_interval: int = 10,
    ) -> "FailureSolveSession":
        """Start a session after preloading the current Failure's full history."""
        if not 1 <= max_outputs <= 50:
            raise ValueError("max_outputs must be between 1 and 50")
        if continuation_interval < 1:
            raise ValueError("continuation_interval must be positive")
        if not 1 <= case_count <= 20:
            raise ValueError("case_count must be between 1 and 20")
        if not 1 <= max_patch_outputs <= 20:
            raise ValueError("max_patch_outputs must be between 1 and 20")
        if not self.model.enabled:
            raise RuntimeError("The Failure Solve model is not configured")
        history = self.memory.failure_history(
            operation_key=request.operation_key,
            failure_id=request.todo.failure_id,
        )
        return FailureSolveSession(
            client=self.client,
            model=self.model,
            http_probe=self.http_probe,
            memory=self.memory,
            patch_agent_factory=self.patch_agent_factory,
            patch_application=self.patch_application,
            openapi_capability=self.openapi_capability,
            reference_values=self.reference_values,
            validator=self.validator,
            tracing_runtime=self.tracing_runtime,
            request=request,
            catalog=catalog,
            config=config,
            active_constraints=active_constraints,
            case_count=case_count,
            random_seed=random_seed,
            max_patch_outputs=max_patch_outputs,
            prepare_patch_updates=prepare_patch_updates,
            failure_history=history,
            system_prompt=self.system_prompt,
            max_outputs=max_outputs,
            continuation_interval=continuation_interval,
        )


class FailureSolveSession:
    """Retain tools, candidates, and budget for one independent Failure."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        memory: SolveMemory,
        patch_agent_factory: PatchAgentFactory,
        patch_application: PatchApplication,
        openapi_capability: OpenAPICapability,
        reference_values: ReferenceValueProvider | None,
        validator: OutputValidator,
        tracing_runtime: TracingRuntime,
        request: FailureSolveRequest,
        catalog: TestCaseCatalog,
        config: OperationGeneratorConfig,
        active_constraints: list[CompiledConstraintPatch],
        case_count: int,
        random_seed: int,
        max_patch_outputs: int,
        prepare_patch_updates: Callable | None,
        failure_history: FailureHistory,
        system_prompt: str,
        max_outputs: int,
        continuation_interval: int,
    ) -> None:
        """Build prompt state without calling the LLM, HTTP, or Patch Agent."""
        self.client = client
        self.model = model
        self.http_probe = http_probe
        self.memory = memory
        self.patch_agent_factory = patch_agent_factory
        self.patch_application = patch_application
        self.openapi_capability = openapi_capability
        self.reference_values = reference_values
        self.validator = validator
        self.tracing_runtime = tracing_runtime
        self.request = request
        self.catalog = catalog
        self.config = config
        self.active_constraints = list(active_constraints)
        self.case_count = case_count
        self.random_seed = random_seed
        self.max_patch_outputs = max_patch_outputs
        self.prepare_patch_updates = prepare_patch_updates
        self.max_outputs = max_outputs
        self.continuation_interval = continuation_interval
        self.outputs_used = 0
        self.candidates: dict[str, PatchCandidate] = {}
        self.queried_parameter_handles: set[str] = set()
        self.parameter_history_for_patch: list[dict] = []
        self.semantic_inputs = build_semantic_input_map(config)
        self.reference_options = [
            AvailableReferenceOption.model_validate(item)
            for item in request.reference_options
        ]
        self.tools = self._build_tools()

        rendered = _solve_context_text(
            request=request,
            config=config,
            active_constraints=active_constraints,
            failure_history=failure_history,
            semantic_inputs=self.semantic_inputs,
            reference_options=self.reference_options,
            catalog_range=catalog.case_range,
        )
        self.context = AgentContext(
            system=system_prompt,
            user=rendered.text,
            limits=ContextLimits(
                system_chars=3_500,
                initial_user_chars=24_000,
                feedback_chars=8_000,
                conversation_chars=48_000,
                required_output_tokens=model.max_tokens,
            ),
            metrics=rendered.metrics,
        )

    def advance(self) -> FailureSolveOutcome:
        """Run until a terminal conclusion or the shared output budget ends.

        A Solve LLM response consumes one output.  Every nested Parameter Patch
        LLM response also consumes the same 50-output budget; each individual
        Patch tool call is additionally capped at 20 outputs.
        """
        with self.tracing_runtime.span(
            "FailureSolveAgent.solve",
            kind="AGENT",
            input_value={
                "operation_key": self.request.operation_key,
                "todo_id": self.request.todo.todo_id,
                "round_number": self.request.round_number,
                "max_outputs": self.max_outputs,
            },
        ) as span:
            for name, value in self.context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            while self.outputs_used < self.max_outputs:
                next_output = self.outputs_used + 1
                checkpoint = (
                    next_output % self.continuation_interval == 0
                    and next_output < self.max_outputs
                )
                if checkpoint:
                    self.context.append_feedback(
                        "CONTINUATION CHECKPOINT\n"
                        "Return action=continue with a genuinely new next_step, "
                        "or finish with apply_patch, no_patch, or conflict. "
                        "Tools are unavailable for this output."
                    )
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=self.context.messages_for_request(self.model),
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        # Tool calls remain provider-owned. When the model
                        # returns a terminal decision instead, the authoritative
                        # DTO schema prevents it from guessing wrapper names or
                        # omitting the terminal fields required by Memory.
                        response_format="json_schema",
                        json_schema=FailureSolveDecision.model_json_schema(),
                        json_schema_name="FailureSolveDecision",
                        tools=[] if checkpoint else self.tools.specs(),
                        tool_choice="none" if checkpoint else "auto",
                        timeout_seconds=self.model.timeout_seconds,
                        reasoning=self.model.reasoning,
                        metadata={"role": "operation_smoke_failure_solve"},
                    )
                )
                self.outputs_used += 1

                if response.tool_calls:
                    errors = self._tool_errors(response, checkpoint=checkpoint)
                    if errors:
                        self._append_correction(response, errors)
                        continue

                    # DeepSeek can return several independent lookup calls in
                    # one assistant message.  They have already been validated
                    # as a bounded, read-only group, so retain the whole group
                    # and attach one provider-required result per call.  Patch
                    # and HTTP tools never enter this branch as a group because
                    # they may create work or mutate the target API.
                    self.context.append_assistant(response)
                    results = (
                        self.tools.execute_many(response.tool_calls)
                        if len(response.tool_calls) > 1
                        else [self.tools.execute(response.tool_calls[0])]
                    )
                    self._apply_query_results(response.tool_calls, results)
                    for result in results:
                        self.context.append_tool_result(
                            result.name,
                            result.tool_call_id,
                            (
                                tool_result_json(result)
                                if result.name
                                in {
                                    CATALOG_QUERY_TOOL_NAME,
                                    HTTP_REQUEST_TOOL_NAME,
                                }
                                else _tool_result_text(result)
                            ),
                        )
                    continue

                decision, errors = self._decision(response)
                if decision is not None:
                    errors.extend(
                        self._decision_errors(
                            decision,
                            checkpoint=checkpoint,
                        )
                    )
                if errors or decision is None:
                    self._append_correction(
                        response,
                        errors or ["The Solve output could not be used."],
                    )
                    continue
                if decision.action == "continue":
                    self.context.append_assistant(response)
                    self.context.append_feedback(
                        "Continue with the stated next_step. The scoped tools "
                        "are available again."
                    )
                    continue

                outcome = self._persist_terminal(decision)
                for name, value in self.context.metrics.trace_attributes().items():
                    span.set_attribute(name, value)
                span.set_output(
                    {
                        "status": outcome.status,
                        "outputs_used": outcome.outputs_used,
                        "solve_attempt_id": outcome.solve_attempt_id,
                    }
                )
                return outcome

            exhausted = FailureSolveOutcome(
                status="solve_budget_exhausted",
                outputs_used=self.outputs_used,
                reason="The Failure Solve output budget was exhausted.",
            )
            for name, value in self.context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            span.set_output(exhausted.model_dump(mode="json"))
            return exhausted

    def _append_correction(
        self,
        response: LLMResponse,
        errors: list[str],
    ) -> None:
        """Add repair guidance without replaying tool calls that never ran.

        A provider requires one tool-result message for every assistant tool
        call retained in the next request. Validation rejects a whole output
        before any tool runs, so retaining those calls would create an invalid
        conversation. Plain structured decisions are safe to retain because
        they do not require matching tool results.
        """
        if not response.tool_calls:
            self.context.append_assistant(response)
        self.context.append_feedback(
            "SOLVE OUTPUT INVALID\n"
            + "\n".join(f"issue | {error}" for error in errors)
            + (
                "\nContinue with one or more read-only query calls, one "
                "Patch/HTTP call, or one complete terminal JSON decision."
            )
        )

    def _build_tools(self) -> AgentToolbox:
        """Bind every current-operation and session dependency for Solve."""
        semantic_handles = sorted(self.semantic_inputs.node_by_handle)
        tools = AgentToolbox(tracing_runtime=self.tracing_runtime)
        tools.register(
            spec=openapi_list_inputs_tool_spec(),
            execute=self.openapi_capability.list_inputs,
        )
        tools.register(
            spec=openapi_get_input_schema_tool_spec(),
            execute=self.openapi_capability.get_input_schema,
        )
        tools.register(
            spec=openapi_get_response_field_schema_tool_spec(),
            execute=self.openapi_capability.get_response_field_schema,
        )
        tools.register(
            spec=_parameter_memory_tool_spec(semantic_handles),
            execute=lambda *, input_handles: self._read_parameter_history(
                list(input_handles)
            ),
        )
        tools.register(
            spec=_patch_tool_spec(semantic_handles),
            execute=lambda **arguments: self._adapt_existing_result(
                self._execute_patch_tool(
                    ToolCall(
                        id="solve-patch",
                        name=_PATCH_TOOL_NAME,
                        arguments=arguments,
                    )
                )
            ),
        )
        tools.register(
            spec=catalog_query_tool_spec(),
            execute=lambda **arguments: {
                "structured": query_catalog(
                    catalog=self.catalog,
                    arguments=arguments,
                )
            },
        )
        tools.register(
            spec=self.http_probe.tool_spec(self.config),
            execute=lambda **arguments: self._adapt_existing_result(
                self.http_probe.execute(
                    config=self.config,
                    tool_call=ToolCall(
                        id="solve-http-probe",
                        name=HTTP_REQUEST_TOOL_NAME,
                        arguments=arguments,
                    ),
                    catalog=self.catalog,
                )
            ),
        )
        return tools

    @staticmethod
    def _adapt_existing_result(result: ToolResult) -> dict[str, Any]:
        """Convert a workflow result into the toolbox's implementation contract."""
        if result.status != "succeeded":
            error = result.error or {}
            raise ToolFailure(
                code=str(error.get("code") or "tool_failed"),
                message=str(error.get("message") or "The tool failed."),
                content=result.content,
                status=(
                    "timed_out"
                    if result.status == "timed_out"
                    else "failed"
                ),
            )
        return {
            "content": result.content,
            "structured": result.structured,
            "artifact_ids": result.artifact_ids,
        }

    def _tool_errors(
        self,
        response: LLMResponse,
        *,
        checkpoint: bool,
    ) -> list[str]:
        """Validate a whole model output before any tool has side effects."""
        errors: list[str] = []
        if checkpoint:
            errors.append("Tools are unavailable at a continuation checkpoint.")
        if response.parsed_json is not None or (
            response.content is not None and response.content.strip()
        ):
            errors.append("Do not mix a tool call with a Solve decision.")

        calls = response.tool_calls
        call_ids = [call.id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            errors.append("Every Solve tool call must have a unique call id.")

        if len(calls) > 1:
            if any(call.name not in _READ_ONLY_QUERY_TOOL_NAMES for call in calls):
                errors.append(
                    "Call exactly one Patch or HTTP tool per model output; "
                    "only read-only Catalog and Parameter Memory queries may "
                    "be grouped."
                )
        if errors:
            return errors

        for call in calls:
            errors.extend(self._single_tool_errors(call))
        return errors

    def _single_tool_errors(self, call: ToolCall) -> list[str]:
        """Validate one call after the surrounding output is proven safe.

        The caller validates the whole group before executing anything.  This
        helper therefore reports argument and ordering mistakes without
        allowing a partly valid group to change session or target state.
        """
        errors: list[str] = []
        if call.name == _MEMORY_TOOL_NAME:
            handles = call.arguments.get("input_handles")
            if (
                not isinstance(handles, list)
                or not handles
                or any(not isinstance(item, str) for item in handles)
            ):
                errors.append("input_handles must be a non-empty string array.")
            else:
                if len(handles) != len(set(handles)):
                    errors.append("input_handles must be unique.")
                for handle in handles:
                    if handle not in self.semantic_inputs.node_by_handle:
                        errors.append(f"Unknown semantic input: {handle}")
        elif call.name == _PATCH_TOOL_NAME:
            try:
                task = self._patch_task(call)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))
            else:
                missing_queries = sorted(
                    set(task.affected_inputs)
                    - self.queried_parameter_handles
                )
                if missing_queries:
                    errors.append(
                        "Query Parameter memory before generating a Patch for: "
                        + ", ".join(missing_queries)
                    )
        elif call.name in {
            CATALOG_QUERY_TOOL_NAME,
            OPENAPI_LIST_INPUTS_TOOL_NAME,
            OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
            OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
        }:
            # The toolbox validates each read-only tool's JSON arguments. The
            # owning Module performs semantic lookup and returns safe failures.
            pass
        else:
            probe_error = self.http_probe.validate(
                config=self.config,
                tool_call=call,
            )
            if probe_error is not None:
                errors.append(probe_error)
        return errors

    def _read_parameter_history(self, handles: list[str]) -> dict[str, Any]:
        """Read and render Parameter history without mutating Solve state."""
        node_ids = [
            self.semantic_inputs.node_by_handle[handle]
            for handle in handles
        ]
        histories = [
            self.memory.parameter_history(
                operation_key=self.request.operation_key,
                input_node_id=node_id,
            )
            for node_id in node_ids
        ]
        rendered = _parameter_history_text(
            handles=handles,
            histories=histories,
            config=self.config,
            handle_by_node=self.semantic_inputs.handle_by_node,
        )
        if rendered.text.startswith("CLIPPED MESSAGE"):
            raise ToolFailure(
                code="history_too_large",
                message=(
                    "Compatibility-critical Patch/conflict history exceeds "
                    "the safe feedback budget."
                ),
                content=(
                    "PARAMETER HISTORY UNAVAILABLE | "
                    "code=history_too_large | patching-blocked=bool:true"
                ),
            )
        prompt_histories = [
            _parameter_history_for_prompt(
                handle=handle,
                history=history,
                handle_by_node=self.semantic_inputs.handle_by_node,
            )
            for handle, history in zip(handles, histories, strict=True)
        ]
        return {
            "content": rendered.text,
            "structured": {"parameters": prompt_histories},
        }

    def _apply_query_results(
        self,
        calls: list[ToolCall],
        results: list[ToolResult],
    ) -> None:
        """Apply successful query bookkeeping after concurrent reads finish."""
        for call, result in zip(calls, results, strict=True):
            if call.name != _MEMORY_TOOL_NAME or result.status != "succeeded":
                continue
            handles = list(call.arguments["input_handles"])
            structured = result.structured
            if not isinstance(structured, dict):
                continue
            parameters = structured.get("parameters")
            if not isinstance(parameters, list):
                continue
            self.queried_parameter_handles.update(handles)
            self.parameter_history_for_patch.extend(parameters)

    def _execute_patch_tool(self, call: ToolCall) -> ToolResult:
        """Run a fresh Patch Agent and retain only a validated local candidate."""
        remaining_outputs = self.max_outputs - self.outputs_used
        if remaining_outputs <= 0:
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                status="failed",
                error={
                    "code": "solve_budget_exhausted",
                    "message": "No Solve outputs remain for Parameter Patch.",
                },
            )
        task = self._patch_task(call)
        with self.tracing_runtime.span(
            "FailureSolveAgent.generate_parameter_patch",
            kind="TOOL",
            input_value={
                "todo_id": task.todo_id,
                "affected_inputs": task.affected_inputs,
            },
        ) as span:
            result = self.patch_agent_factory.create().run(
                task=task,
                config=self.config,
                active_constraints=self.active_constraints,
                case_count=self.case_count,
                reference_values=self.reference_values,
                reference_options=self.reference_options,
                random_seed=self.random_seed,
                max_outputs=min(self.max_patch_outputs, remaining_outputs),
            )
            self.outputs_used += result.outputs_used
            if isinstance(result, ParameterPatchFailure):
                span.set_output(
                    {
                        "status": "failed",
                        "patch_outputs": result.outputs_used,
                    }
                )
                return ToolResult(
                    tool_call_id=call.id,
                    name=call.name,
                    status="failed",
                    content=(
                        "PATCH TOOL FAILED | "
                        f"code={result.reason} | outputs=int:{result.outputs_used} | "
                        f'message="{_single_line("; ".join(result.errors))}"'
                    ),
                    error={
                        "code": result.reason,
                        "message": "; ".join(result.errors)
                        or "Parameter Patch did not produce a valid candidate.",
                    },
                )

            assert isinstance(result, ValidatedParameterPatch)
            candidate_ref = f"P{len(self.candidates) + 1}"
            preview = preview_generator_patch(
                self.config,
                result.patch.updates,
            )
            before = _generator_summary(
                self.config,
                affected_inputs=task.affected_inputs,
            )
            after = _generator_summary(
                preview,
                affected_inputs=task.affected_inputs,
            )
            candidate = PatchCandidate(
                candidate_ref=candidate_ref,
                patch=result.patch,
                before_generators=before,
                after_generators=after,
                samples=result.samples,
                patch_outputs=result.outputs_used,
            )
            self.candidates[candidate_ref] = candidate
            span.set_output(
                {
                    "status": "validated",
                    "candidate_ref": candidate_ref,
                    "patch_outputs": result.outputs_used,
                }
            )
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                status="succeeded",
                content=_patch_candidate_text(candidate),
                structured=candidate.model_dump(mode="json"),
            )

    def _patch_task(self, call: ToolCall) -> ParameterPatchTask:
        """Validate Patch-tool arguments against the current semantic inputs."""
        arguments = call.arguments
        affected = arguments["affected_inputs"]
        if not isinstance(affected, list):
            raise TypeError("affected_inputs must be an array")
        for handle in affected:
            if handle not in self.semantic_inputs.node_by_handle:
                raise ValueError(f"Unknown semantic input: {handle}")
        return ParameterPatchTask(
            todo_id=self.request.todo.todo_id,
            failure=self.request.todo.failure,
            root_cause=arguments["root_cause"],
            affected_inputs=affected,
            desired_behavior=arguments["desired_behavior"],
            acceptance_criteria=arguments["acceptance_criteria"],
            # Patch Agent receives every related history read in this session.
            # This is the concrete mechanism behind "a replacement Generator
            # must also consider previously discovered Failures."
            prior_attempts=list(self.parameter_history_for_patch),
        )

    def _decision(
        self,
        response: LLMResponse,
    ) -> tuple[FailureSolveDecision | None, list[str]]:
        """Parse one strict non-tool Solve response."""
        validation = self.validator.validate(
            response=response,
            output_model=FailureSolveDecision,
        )
        if not validation.valid:
            return None, [
                (
                    f"{issue.location}: {issue.message}"
                    if issue.location
                    else issue.message
                )
                for issue in validation.errors
            ]
        return (
            FailureSolveDecision.model_validate(validation.validated_object),
            [],
        )

    def _decision_errors(
        self,
        decision: FailureSolveDecision,
        *,
        checkpoint: bool,
    ) -> list[str]:
        """Reject forged candidates, unknown Parameters, and misplaced continue."""
        errors: list[str] = []
        if decision.action == "continue" and not checkpoint:
            errors.append(
                "action=continue is available only at a continuation checkpoint."
            )
        if checkpoint and decision.action not in {
            "continue",
            "apply_patch",
            "no_patch",
            "conflict",
        }:
            errors.append("Checkpoint decision is not terminal or continue.")
        if (
            decision.action == "apply_patch"
            and decision.candidate_ref not in self.candidates
        ):
            errors.append(
                f"{decision.candidate_ref} is not a candidate from this session."
            )
        supplied_handles = [
            item.input_handle for item in decision.parameters
        ]
        if len(supplied_handles) != len(set(supplied_handles)):
            errors.append("terminal Parameter causes must be unique.")
        for handle in supplied_handles:
            if handle not in self.semantic_inputs.node_by_handle:
                errors.append(f"Unknown semantic input: {handle}")
        return errors

    def _persist_terminal(
        self,
        decision: FailureSolveDecision,
    ) -> FailureSolveOutcome:
        """Write one terminal Solve Attempt and atomically apply its Patch."""
        assert decision.action != "continue"
        assert decision.trigger_conditions is not None
        assert decision.root_cause is not None
        assert decision.solution is not None
        assert decision.evidence_source is not None
        parameters = [
            SolveAttemptParameterWrite(
                input_node_id=self.semantic_inputs.node_by_handle[
                    item.input_handle
                ],
                cause_summary=item.cause_summary,
            )
            for item in decision.parameters
        ]

        if decision.action == "apply_patch":
            assert decision.candidate_ref is not None
            candidate = self.candidates[decision.candidate_ref]
            patch = candidate.patch
            if self.prepare_patch_updates is not None:
                # Reference-backed generators may require a Behavior Monitor
                # registration immediately before persistence.  Solve still
                # chooses the candidate; this callback performs only that
                # deterministic runtime preparation.
                prepared_updates = self.prepare_patch_updates(
                    self.config,
                    patch.updates,
                    patch.selected_reference_options,
                )
                patch = patch.model_copy(
                    update={"updates": prepared_updates}
                )
            try:
                applied = self.patch_application.apply(
                    current=self.config,
                    expected_constraints=self.active_constraints,
                    patch=patch,
                    attempt=PatchSolveAttempt(
                        operation_key=self.request.operation_key,
                        failure_id=self.request.todo.failure_id,
                        round_number=self.request.round_number,
                        trigger_conditions=decision.trigger_conditions,
                        root_cause=decision.root_cause,
                        solution=decision.solution,
                        evidence_source=decision.evidence_source,
                        parameters=parameters,
                    ),
                )
            except GeneratorConfigConcurrentWrite:
                # The candidate was valid for the state shown to the Agent, but
                # that state changed before commit.  This is durable domain
                # evidence, not a partial technical failure: the Patch
                # transaction has rolled back, then a conflict Attempt is
                # appended in its own transaction.
                conflict_reason = (
                    "Current Generator or Constraint state changed before "
                    "the selected Patch could commit."
                )
                solve_attempt_id = self.memory.record_solve_attempt(
                    SolveAttemptWrite(
                        operation_key=self.request.operation_key,
                        failure_id=self.request.todo.failure_id,
                        round_number=self.request.round_number,
                        outcome="conflict",
                        trigger_conditions=decision.trigger_conditions,
                        root_cause=decision.root_cause,
                        solution=decision.solution,
                        evidence_source=decision.evidence_source,
                        parameters=parameters,
                        conflict_reason=conflict_reason,
                    )
                )
                return FailureSolveOutcome(
                    status="conflict",
                    outputs_used=self.outputs_used,
                    solve_attempt_id=solve_attempt_id,
                    reason=conflict_reason,
                )
            self.config = applied.config
            return FailureSolveOutcome(
                status="applied_patch",
                outputs_used=self.outputs_used,
                solve_attempt_id=applied.solve_attempt_id,
                generator_change_event_id=applied.generator_change_event_id,
                applied_patch=candidate,
                active_constraints=[
                    CompiledConstraintPatch(
                        constraint_id=item.id,
                        kind=item.kind,
                        constraint=item.constraint,
                    )
                    for item in applied.constraints
                ],
            )

        solve_attempt_id = self.memory.record_solve_attempt(
            SolveAttemptWrite(
                operation_key=self.request.operation_key,
                failure_id=self.request.todo.failure_id,
                round_number=self.request.round_number,
                outcome=decision.action,
                trigger_conditions=decision.trigger_conditions,
                root_cause=decision.root_cause,
                solution=decision.solution,
                evidence_source=decision.evidence_source,
                parameters=parameters,
                conflict_reason=decision.conflict_reason,
            )
        )
        return FailureSolveOutcome(
            status=decision.action,
            outputs_used=self.outputs_used,
            solve_attempt_id=solve_attempt_id,
            reason=(
                decision.conflict_reason
                if decision.action == "conflict"
                else decision.solution
            ),
        )


def _system_prompt() -> str:
    """Describe the short evidence → history → candidate → terminal protocol."""
    return """# Role

Investigate exactly one Operation Smoke Failure.

# Stages

1. Read the representative `TC*` reference and preloaded Failure history.
2. Query OpenAPI tools for input or response-field Schemas and
   `query_test_case_catalog` for exact request values, response fields, or
   Failure Messages only as needed. Independent OpenAPI, Catalog, and Parameter
   Memory reads may be grouped in one output.
3. Before patching an input, call `lookup_parameter_history`.
4. Probe HTTP only when existing evidence cannot distinguish the root cause.
5. Call `generate_parameter_patch` with confirmed, testable requirements.
6. Return one terminal `FailureSolveDecision` JSON object.

# Rules

- One output may group only read-only OpenAPI/Catalog/Parameter Memory queries.
- Patch and HTTP outputs call exactly one tool.
- A tool output and a terminal decision are never mixed.
- Copy semantic handles exactly from tool-schema enums.
- Slash-separated `input_node_id` values are not semantic handles.
- A replacement must remain compatible with prior Patches and conflicts.
- `generate_parameter_patch` has no side effects and returns a session `P` ref.
- A successful HTTP probe returns a new `TC*`; query the Catalog for details.
- HTTP probes may mutate the exact current operation and are not rolled back.
- `apply_patch` is a final decision action, not a tool.
- Other terminal actions are `no_patch` and `conflict`.
- Use `candidate_ref` only with `apply_patch`.
- Do not invent aliases or database IDs.
"""


def _parameter_memory_tool_spec(
    semantic_handles: list[str],
) -> ToolSpec:
    """Describe history lookup with exact handles valid for this operation."""
    return ToolSpec(
        name=_MEMORY_TOOL_NAME,
        description=(
            "Read prior Failures, causes, conflicts, and applied Patches for one "
            "or more semantic inputs in this operation. Copy handles exactly from "
            "the schema enum."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "input_handles": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": semantic_handles,
                    },
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["input_handles"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"parameters": {"type": "array"}},
            "required": ["parameters"],
            "additionalProperties": False,
        },
    )


def _patch_tool_spec(
    semantic_handles: list[str],
) -> ToolSpec:
    """Describe Patch requirements with operation-valid semantic handles."""
    return ToolSpec(
        name=_PATCH_TOOL_NAME,
        description=(
            "Ask Parameter Patch Agent to build and locally validate a Generator "
            "or cross-Parameter Constraint candidate. This tool has no side effects. "
            "Copy affected input handles exactly from the schema enum."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "root_cause": {"type": "string", "minLength": 1},
                "affected_inputs": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": semantic_handles,
                    },
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "desired_behavior": {"type": "string", "minLength": 1},
                "acceptance_criteria": {"type": "string", "minLength": 1},
            },
            "required": [
                "root_cause",
                "affected_inputs",
                "desired_behavior",
                "acceptance_criteria",
            ],
            "additionalProperties": False,
        },
        output_schema=PatchCandidate.model_json_schema(),
    )


def _solve_context_text(
    *,
    request: FailureSolveRequest,
    config: OperationGeneratorConfig,
    active_constraints: list[CompiledConstraintPatch],
    failure_history: FailureHistory,
    semantic_inputs,
    reference_options: list[AvailableReferenceOption],
    catalog_range: str,
):
    """Render the representative case reference, constraints, and Memory."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section("TASK")
    writer.record(
        request.todo.todo_id,
        operation=request.operation_key,
        method=config.snapshot.method,
        path=config.snapshot.path,
        active_request_media_type=config.active_media_type,
        round=request.round_number,
        failure=request.todo.failure,
        representative_case=request.todo.test_case_id,
        catalog=catalog_range,
    )

    writer.section("ACTIVE CONSTRAINTS", untrusted=True)
    if not active_constraints:
        writer.record("none", count=0)
    for constraint in active_constraints:
        writer.record(
            constraint.constraint_id,
            kind=constraint.kind,
            expression=_semantic_constraint(
                constraint.constraint.model_dump(mode="python"),
                semantic_inputs.handle_by_node,
            ),
        )

    writer.section("REFERENCE ALIASES", untrusted=True)
    if not reference_options:
        writer.record("none", count=0)
    for index, option in enumerate(reference_options, start=1):
        writer.record(
            f"R{index}",
            input=semantic_inputs.handle_by_node.get(
                option.input_node_id,
                "<inactive-input>",
            ),
            kind=option.kind,
            values=option.value_count,
            resource=option.canonical_resource,
            value_name=option.value_name,
            producers=", ".join(option.producer_operation_keys) or None,
            status=option.producer_status_code,
            media=option.producer_media_type,
            field=option.source_field,
            selector=option.source_selector,
        )

    _write_failure_history(
        writer,
        failure_history,
        handle_by_node=semantic_inputs.handle_by_node,
    )
    return writer.render(max_chars=24_000)


def _write_failure_history(
    writer: CompactTextWriter,
    history: FailureHistory,
    *,
    handle_by_node,
) -> None:
    """Write compatibility-critical outcomes and aggregate old no-Patch noise."""
    writer.section("CURRENT FAILURE MEMORY", untrusted=True)
    outcomes: dict[str, int] = {}
    for attempt in history.attempts:
        outcomes[attempt.outcome] = outcomes.get(attempt.outcome, 0) + 1
    writer.record(
        "summary",
        failure=history.summary,
        occurrences=history.occurrence_count,
        solve_attempts=len(history.attempts),
        outcomes=outcomes,
    )

    detailed_ids = {
        item.solve_attempt_id
        for item in history.attempts[-5:]
    }
    older_no_patch: dict[str, int] = {}
    for attempt in history.attempts:
        handles = [
            handle_by_node.get(parameter.input_node_id, "<inactive-input>")
            for parameter in attempt.parameters
        ]
        compatibility_critical = attempt.outcome in {
            "applied_patch",
            "conflict",
        }
        if compatibility_critical or attempt.solve_attempt_id in detailed_ids:
            writer.record(
                f"round-{attempt.round_number}",
                outcome=attempt.outcome,
                trigger=attempt.trigger_conditions,
                root_cause=attempt.root_cause,
                solution=attempt.solution,
                parameters=handles,
                conflict=attempt.conflict_reason,
                generator_change_event=(
                    attempt.generator_change.event_id
                    if attempt.generator_change is not None
                    else None
                ),
            )
            if attempt.generator_change is not None:
                writer.detail(
                    "generator-change",
                    {
                        "generators": attempt.generator_change.generator_changes,
                        "constraints": attempt.generator_change.constraint_changes,
                    },
                )
        else:
            signature = (
                ",".join(sorted(handles)) or "no-parameter"
            ) + "|" + attempt.root_cause.casefold()[:120]
            older_no_patch[signature] = older_no_patch.get(signature, 0) + 1
    for signature, count in sorted(older_no_patch.items()):
        writer.record(
            "older-no-patch",
            signature=signature,
            count=count,
            required=False,
        )


def _parameter_history_text(
    *,
    handles: list[str],
    histories: list[ParameterHistory],
    config: OperationGeneratorConfig,
    handle_by_node,
):
    """Render applied/conflict facts first and old no-Patch records optionally."""
    writer = CompactTextWriter(max_value_chars=800)
    config_by_node = {item.input_node_id: item for item in config.configs}
    for handle, history in zip(handles, histories, strict=True):
        writer.section(f"PARAMETER {handle}", untrusted=True)
        current = config_by_node.get(history.input_node_id)
        writer.record(
            "current",
            generator=(
                current.strategy.model_dump(mode="python")
                if current is not None
                else None
            ),
            inclusion_probability=(
                current.inclusion_probability
                if current is not None
                else None
            ),
            related_failures=len(history.failures),
        )
        recent_no_patch: list[tuple[str, Any]] = []
        for failure in history.failures:
            writer.text("failure", failure.summary)
            for attempt in failure.attempts:
                item = (
                    attempt.root_cause,
                    attempt,
                )
                if attempt.outcome in {"applied_patch", "conflict"}:
                    writer.record(
                        f"round-{attempt.round_number}",
                        outcome=attempt.outcome,
                        cause=attempt.root_cause,
                        solution=attempt.solution,
                        conflict=attempt.conflict_reason,
                        parameters=[
                            handle_by_node.get(
                                parameter.input_node_id,
                                "<inactive-input>",
                            )
                            for parameter in attempt.parameters
                        ],
                        generator_change_event=(
                            attempt.generator_change.event_id
                            if attempt.generator_change is not None
                            else None
                        ),
                    )
                    if attempt.generator_change is not None:
                        writer.detail(
                            "generator-change",
                            {
                                "generators": attempt.generator_change.generator_changes,
                                "constraints": attempt.generator_change.constraint_changes,
                            },
                        )
                else:
                    recent_no_patch.append(item)
        for cause, attempt in recent_no_patch[-5:]:
            writer.record(
                f"no-patch-round-{attempt.round_number}",
                cause=cause,
                solution=attempt.solution,
                required=False,
            )
        if len(recent_no_patch) > 5:
            writer.record(
                "older-no-patch",
                count=len(recent_no_patch) - 5,
                required=False,
            )
    return writer.render(max_chars=8_000)


def _parameter_history_for_prompt(
    *,
    handle: str,
    history: ParameterHistory,
    handle_by_node,
) -> dict:
    """Convert a Parameter projection into a storage-ID-free tool result."""
    payload = history.model_dump(mode="json")
    payload.pop("input_node_id", None)
    payload["input_handle"] = handle
    for failure in payload["failures"]:
        failure.pop("failure_id", None)
        for attempt in failure["attempts"]:
            attempt.pop("solve_attempt_id", None)
            for parameter in attempt["parameters"]:
                node_id = parameter.pop("input_node_id")
                parameter["input_handle"] = handle_by_node.get(
                    node_id,
                    "<inactive-input>",
                )
    return payload


def _semantic_constraint(value: Any, handle_by_node) -> Any:
    """Translate internal Constraint input IDs before text reaches Solve."""
    if isinstance(value, list):
        return [
            _semantic_constraint(item, handle_by_node)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    output = {
        key: _semantic_constraint(item, handle_by_node)
        for key, item in value.items()
        if key != "input_node_id"
    }
    if "input_node_id" in value:
        output["input"] = handle_by_node.get(
            value["input_node_id"],
            "<inactive-input>",
        )
    return output


def _tool_result_text(result: ToolResult) -> str:
    """Project any scoped tool result into bounded text for the Solve model."""
    if result.content and result.name in {_MEMORY_TOOL_NAME, _PATCH_TOOL_NAME}:
        return result.content
    writer = CompactTextWriter(max_value_chars=1_200)
    writer.section(
        "HTTP PROBE" if result.status == "succeeded" else "TOOL FAILURE",
        untrusted=True,
    )
    writer.record(
        result.name,
        status=result.status,
        error_code=(
            result.error.get("code")
            if isinstance(result.error, dict)
            else None
        ),
        error_message=(
            result.error.get("message")
            if isinstance(result.error, dict)
            else None
        ),
    )
    if isinstance(result.structured, dict):
        writer.json_block("HTTP probe result", result.structured)
    elif result.structured is not None:
        writer.text("result", result.structured)
    if result.content:
        writer.text("body", result.content)
    return writer.render(max_chars=8_000).text


def _patch_candidate_text(candidate: PatchCandidate) -> str:
    """Summarize a validated local Patch without dumping its DTO or all samples."""
    writer = CompactTextWriter(max_value_chars=600)
    writer.section("PATCH CANDIDATE")
    affected = sorted(
        {
            *candidate.before_generators,
            *candidate.after_generators,
        }
    )
    writer.record(
        candidate.candidate_ref,
        affected_inputs=affected,
        patch_outputs=candidate.patch_outputs,
        sample_count=len(candidate.samples),
        constraint_count=len(candidate.patch.constraints),
    )
    for handle in affected:
        writer.record(
            handle,
            before=candidate.before_generators.get(handle),
            after=candidate.after_generators.get(handle),
        )
    for index, sample in enumerate(candidate.samples[:3], start=1):
        writer.detail(f"sample-{index}", sample)
    if len(candidate.samples) > 3:
        writer.record(
            "remaining-samples",
            count=len(candidate.samples) - 3,
            summary=_sample_value_summary(candidate.samples[3:]),
        )
    return writer.render(max_chars=8_000).text


def _sample_value_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe omitted candidate values by type and presence only."""
    summary: dict[str, Any] = {}
    handles = {
        handle
        for sample in samples
        for handle in (sample.get("values") or {})
    }
    for handle in sorted(handles):
        values = [
            sample["values"][handle]
            for sample in samples
            if (sample.get("present") or {}).get(handle)
        ]
        numeric = [
            value
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        summary[handle] = {
            "present": len(values),
            "types": sorted({type(value).__name__ for value in values}),
            "minimum": min(numeric) if numeric else None,
            "maximum": max(numeric) if numeric else None,
        }
    return summary


def _single_line(value: str) -> str:
    """Keep a short technical error inside one compact record."""
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _generator_summary(
    config: OperationGeneratorConfig,
    *,
    affected_inputs: list[str],
) -> dict[str, dict]:
    """Project Generator state by semantic handle for model review and memory."""
    semantic = build_semantic_input_map(config)
    configs_by_node = {
        item.input_node_id: item for item in config.configs
    }
    return {
        handle: configs_by_node[
            semantic.node_by_handle[handle]
        ].model_dump(mode="json")
        for handle in affected_inputs
    }
