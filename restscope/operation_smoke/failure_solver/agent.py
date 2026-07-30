"""Investigate one Failure and finish with one durable terminal decision.

Failure Solve is the Agent that decides which Parameters caused a Failure and
whether a Generator Patch should be applied.  It can use three scoped tools:
read Parameter history, probe only the current HTTP operation, and run a fresh
Parameter Patch Agent.  Patch candidates remain session-local and have no side
effects until the model returns ``apply_patch`` with a valid candidate reference.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from restscope.llm import (
    LLMClient,
    LLMMessage,
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
    InvestigationParameterWrite,
    InvestigationWrite,
    ParameterHistory,
    PatchInvestigation,
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
from restscope.operation_smoke.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)
from restscope.testing import (
    OperationGeneratorConfig,
    ReferenceValueProvider,
    build_semantic_input_map,
    preview_generator_patch,
)

from .schemas import (
    FailureSolveDecision,
    FailureSolveOutcome,
    FailureSolveRequest,
    PatchCandidate,
)


_MEMORY_TOOL_NAME = "lookup_parameter_history"
_PATCH_TOOL_NAME = "generate_parameter_patch"


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
    ) -> ToolResult:
        """Execute a previously scope-checked diagnostic request."""
        ...


class SolveMemory(Protocol):
    """Describe the memory reads and non-Patch write owned by Solve runtime."""

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Load the complete history of one operation-scoped Failure."""
        ...

    def lookup_parameter_history(
        self,
        operation_key: str,
        input_node_ids: list[str],
    ) -> list[ParameterHistory]:
        """Load past causes and repairs linked to selected operation inputs."""
        ...

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append a validated no-Patch or conflict Investigation."""
        ...


class PatchAgentFactory(Protocol):
    """Create a fresh Patch Agent for each tool invocation."""

    def create(self) -> ParameterPatchAgent:
        """Return a fresh Agent with an empty proposal conversation."""
        ...


class PatchApplication(Protocol):
    """Atomically change Generator state and record an applied Investigation."""

    def apply(
        self,
        *,
        expected_revision: int,
        patch: GeneratorPatchDraft,
        samples: list[dict[str, Any]],
        before_generators: dict[str, Any],
        after_generators: dict[str, Any],
        investigation: PatchInvestigation,
    ) -> AppliedSmokePatch:
        """Commit one chosen session candidate and its explanation together."""
        ...


class FailureSolveAgent:
    """Create one isolated LLM Investigation for one Planner Failure."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        http_probe: HTTPProbe,
        memory: SolveMemory,
        patch_agent_factory: PatchAgentFactory,
        patch_application: PatchApplication,
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
        self.reference_values = reference_values
        self.system_prompt = system_prompt or _system_prompt()
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def start(
        self,
        request: FailureSolveRequest,
        *,
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
        history = self.memory.lookup_failure_history(
            request.operation_key,
            [request.todo.failure_id],
        )
        return FailureSolveSession(
            client=self.client,
            model=self.model,
            http_probe=self.http_probe,
            memory=self.memory,
            patch_agent_factory=self.patch_agent_factory,
            patch_application=self.patch_application,
            reference_values=self.reference_values,
            validator=self.validator,
            tracing_runtime=self.tracing_runtime,
            request=request,
            config=config,
            active_constraints=active_constraints,
            case_count=case_count,
            random_seed=random_seed,
            max_patch_outputs=max_patch_outputs,
            prepare_patch_updates=prepare_patch_updates,
            failure_history=history[0],
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
        reference_values: ReferenceValueProvider | None,
        validator: OutputValidator,
        tracing_runtime: TracingRuntime,
        request: FailureSolveRequest,
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
        self.reference_values = reference_values
        self.validator = validator
        self.tracing_runtime = tracing_runtime
        self.request = request
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

        prompt_request = request.model_dump(
            mode="json",
            exclude={"todo": {"failure_id"}},
        )
        prompt_request["failure_history"] = _failure_history_for_prompt(
            failure_history,
            handle_by_node=self.semantic_inputs.handle_by_node,
        )
        fitted = fit_prompt_context(
            required=prompt_request,
            history=[],
            model=model,
        )
        self.messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(
                role="user",
                content=json.dumps(
                    fitted.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
        ]

    def advance(self) -> FailureSolveOutcome:
        """Run until a terminal conclusion or the shared output budget ends.

        A Solve LLM response consumes one output.  Every nested Parameter Patch
        LLM response also consumes the same 50-output budget; each individual
        Patch tool call is additionally capped at 20 outputs.
        """
        with self.tracing_runtime.span(
            "FailureSolveAgent.investigate",
            kind="AGENT",
            input_value={
                "operation_key": self.request.operation_key,
                "todo_id": self.request.todo.todo_id,
                "round_number": self.request.round_number,
                "max_outputs": self.max_outputs,
            },
        ) as span:
            while self.outputs_used < self.max_outputs:
                next_output = self.outputs_used + 1
                checkpoint = (
                    next_output % self.continuation_interval == 0
                    and next_output < self.max_outputs
                )
                if checkpoint:
                    self.messages.append(
                        LLMMessage(
                            role="user",
                            content=(
                                "Continuation checkpoint: return action=continue "
                                "with a genuinely new next_step, or finish with "
                                "apply_patch, no_patch, or conflict. Tools are "
                                "unavailable for this output."
                            ),
                        )
                    )
                response = self.client.invoke(
                    LLMRequest(
                        provider=self.model.provider,
                        model=self.model.model,
                        messages=fit_message_context(
                            self.messages,
                            model=self.model,
                        ).messages,
                        temperature=0,
                        max_tokens=self.model.max_tokens,
                        response_format="json",
                        tools=[] if checkpoint else self._tool_specs(),
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
                        _append_correction(self.messages, response, errors)
                        continue
                    call = response.tool_calls[0]
                    self.messages.append(
                        LLMMessage(
                            role="assistant",
                            content="",
                            tool_calls=[call],
                        )
                    )
                    result = self._execute_tool(call)
                    self.messages.append(
                        LLMMessage(
                            role="tool",
                            name=result.name,
                            tool_call_id=result.tool_call_id,
                            content=json.dumps(
                                result.model_dump(mode="json", exclude_none=True),
                                ensure_ascii=False,
                                separators=(",", ":"),
                                default=str,
                            ),
                        )
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
                    _append_correction(
                        self.messages,
                        response,
                        errors or ["The Solve output could not be used."],
                    )
                    continue
                if decision.action == "continue":
                    self.messages.extend(
                        (
                            LLMMessage(
                                role="assistant",
                                content=json.dumps(
                                    decision.model_dump(
                                        mode="json",
                                        exclude_none=True,
                                    ),
                                    separators=(",", ":"),
                                ),
                            ),
                            LLMMessage(
                                role="user",
                                content=(
                                    "Continue with the stated next_step. The "
                                    "scoped tools are available again."
                                ),
                            ),
                        )
                    )
                    continue

                outcome = self._persist_terminal(decision)
                span.set_output(
                    {
                        "status": outcome.status,
                        "outputs_used": outcome.outputs_used,
                        "active_config_revision": outcome.active_config_revision,
                    }
                )
                return outcome

            exhausted = FailureSolveOutcome(
                status="solve_budget_exhausted",
                outputs_used=self.outputs_used,
                reason="The Failure Solve output budget was exhausted.",
            )
            span.set_output(exhausted.model_dump(mode="json"))
            return exhausted

    def _tool_specs(self) -> list[ToolSpec]:
        """Return capabilities whose schemas expose this operation's handles."""
        semantic_handles = sorted(self.semantic_inputs.node_by_handle)
        return [
            _parameter_memory_tool_spec(semantic_handles),
            _patch_tool_spec(semantic_handles),
            self.http_probe.tool_spec(self.config),
        ]

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
        if len(response.tool_calls) != 1:
            errors.append("Call exactly one Solve tool per model output.")
        if response.parsed_json is not None or (
            response.content is not None and response.content.strip()
        ):
            errors.append("Do not mix a tool call with a Solve decision.")
        if len(response.tool_calls) != 1:
            return errors

        call = response.tool_calls[0]
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
        else:
            probe_error = self.http_probe.validate(
                config=self.config,
                tool_call=call,
            )
            if probe_error is not None:
                errors.append(probe_error)
        return errors

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        """Dispatch one prevalidated tool and return a sanitized result."""
        if call.name == _MEMORY_TOOL_NAME:
            return self._execute_memory_tool(call)
        if call.name == _PATCH_TOOL_NAME:
            return self._execute_patch_tool(call)
        return self.http_probe.execute(config=self.config, tool_call=call)

    def _execute_memory_tool(self, call: ToolCall) -> ToolResult:
        """Resolve semantic handles, query related Failures, and hide row IDs."""
        handles = list(call.arguments["input_handles"])
        node_ids = [
            self.semantic_inputs.node_by_handle[handle]
            for handle in handles
        ]
        with self.tracing_runtime.span(
            "FailureSolveAgent.lookup_parameter_history",
            kind="TOOL",
            input_value={"input_handles": handles},
        ) as span:
            histories = self.memory.lookup_parameter_history(
                self.request.operation_key,
                node_ids,
            )
            prompt_histories = [
                _parameter_history_for_prompt(
                    handle=handle,
                    history=history,
                    handle_by_node=self.semantic_inputs.handle_by_node,
                )
                for handle, history in zip(handles, histories, strict=True)
            ]
            self.queried_parameter_handles.update(handles)
            self.parameter_history_for_patch.extend(prompt_histories)
            span.set_output({"parameter_count": len(prompt_histories)})
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            status="succeeded",
            structured={"parameters": prompt_histories},
        )

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
                    error={
                        "code": result.reason,
                        "message": "; ".join(result.errors)
                        or "Parameter Patch did not produce a valid candidate.",
                    },
                    metadata={"patch_outputs": result.outputs_used},
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
        """Write one terminal Investigation, applying a selected Patch atomically."""
        assert decision.action != "continue"
        assert decision.trigger_conditions is not None
        assert decision.root_cause is not None
        assert decision.solution is not None
        assert decision.evidence_source is not None
        parameters = [
            InvestigationParameterWrite(
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
            applied = self.patch_application.apply(
                expected_revision=self.config.revision,
                patch=patch,
                samples=candidate.samples,
                before_generators=candidate.before_generators,
                after_generators=candidate.after_generators,
                investigation=PatchInvestigation(
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
            self.config = applied.config
            return FailureSolveOutcome(
                status="applied_patch",
                outputs_used=self.outputs_used,
                investigation_id=applied.investigation_id,
                active_config_revision=applied.config.revision,
                applied_patch=candidate,
                active_constraints=candidate.patch.constraints,
            )

        investigation_id = self.memory.record_investigation(
            InvestigationWrite(
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
            investigation_id=investigation_id,
            active_config_revision=self.config.revision,
            reason=(
                decision.conflict_reason
                if decision.action == "conflict"
                else decision.solution
            ),
        )


def _system_prompt() -> str:
    """Describe Solve's decision authority and durable safety rules."""
    return (
        "Investigate exactly one Operation Smoke Failure. Decide which semantic "
        "request inputs caused it and how to resolve it. You may query Parameter "
        "memory, probe only the current HTTP operation, and call "
        "generate_parameter_patch multiple times. Call exactly one tool per model "
        "output; never combine parallel tool calls. For Parameter tools, copy only "
        "the exact dotted semantic handles enumerated by their input schemas. "
        "Internal slash-separated input_node_id values are not semantic handles. "
        "Before requesting a Patch for an input, query that input's history and "
        "ensure the new Generator also serves earlier related Failures. Probe HTTP "
        "only when current Batch and memory evidence cannot distinguish the root "
        "cause. If requirements cannot coexist, return conflict and do not apply "
        "anything. A Patch tool result is only a local candidate; finish with "
        "apply_patch(candidate_ref), no_patch, or conflict. Every terminal decision "
        "must include trigger_conditions, root_cause, solution, evidence_source, "
        "and Parameter cause summaries. Do not invent candidate references or "
        "database IDs."
    )


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
        read_only=True,
        risk_level="low",
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
        read_only=True,
        risk_level="low",
    )


def _failure_history_for_prompt(
    history: FailureHistory,
    *,
    handle_by_node,
) -> dict:
    """Remove database identities and translate Parameter IDs to handles."""
    payload = history.model_dump(mode="json")
    payload.pop("failure_id", None)
    for investigation in payload["investigations"]:
        investigation.pop("investigation_id", None)
        for parameter in investigation["parameters"]:
            node_id = parameter.pop("input_node_id")
            parameter["input_handle"] = handle_by_node.get(
                node_id,
                "<inactive-input>",
            )
    return payload


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
        for investigation in failure["investigations"]:
            investigation.pop("investigation_id", None)
            for parameter in investigation["parameters"]:
                node_id = parameter.pop("input_node_id")
                parameter["input_handle"] = handle_by_node.get(
                    node_id,
                    "<inactive-input>",
                )
    return payload


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


def _append_correction(
    messages: list[LLMMessage],
    response: LLMResponse,
    errors: list[str],
) -> None:
    """Keep invalid output and focused correction guidance in-session."""
    messages.extend(
        (
            LLMMessage(
                role="assistant",
                content=json.dumps(
                    response.parsed_json
                    if response.parsed_json is not None
                    else {
                        "content": response.content,
                        "tool_calls": [
                            call.model_dump(mode="json")
                            for call in response.tool_calls
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    "The previous Solve output could not be used:\n"
                    + "\n".join(f"- {error}" for error in errors)
                    + "\nContinue with one valid tool call or one complete "
                    "terminal decision."
                ),
            ),
        )
    )
