"""Run one continuous Agent-owned Failure Resolution session.

The Agent receives deterministic exact Failure references, investigates through
bounded tools, and repeatedly rewrites one reference-only worklist. This module
does not persist draft state or interpret semantic grouping. It asks a trusted
finalizer to validate and atomically commit only after the model announces that
the current worklist is ready.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import json
from typing import Any, Protocol

from restscope.capabilities import (
    AgentToolbox,
    HTTP_REQUEST_TOOL_NAME,
    OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
    OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
    OPENAPI_LIST_INPUTS_TOOL_NAME,
    OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
    OpenAPICapability,
    ToolFailure,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
    openapi_list_response_fields_tool_spec,
)
from restscope.context import AgentContext, CompactTextWriter, ContextLimits
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
from restscope.operation_smoke.output_limit import (
    ModelOutputLimit,
    ModelOutputLimitExceeded,
)
from restscope.operation_smoke.memory import (
    ParameterHistory,
    SolveAttemptParameterWrite,
)
from restscope.operation_smoke.parameter_patch import (
    CompiledConstraintPatch,
    GeneratorPatchDraft,
    ParameterPatchCoordinator,
    ParameterPatchTask,
)
from restscope.operation_smoke.test_case_catalog import (
    TEST_CASE_TOOL_NAMES,
    TestCaseCatalog,
    register_test_case_tools,
    tool_result_json,
)
from restscope.testing import (
    OperationGeneratorConfig,
    ReferenceValueProvider,
    build_semantic_input_map,
    preview_generator_patch,
)

from .candidates import (
    READ_CANDIDATE_TOOL_NAME,
    PatchCandidateRegistry,
    PatchCandidateSummary,
    register_candidate_read_tool,
)
from .compact import (
    FailureResolutionCompactAgent,
    FailureResolutionCompactError,
)
from .prompts import failure_resolution_system_prompt, failure_source_prompt
from .schemas import (
    FailureResolutionFinish,
    FailureResolutionOutcome,
    FailureResolutionRequest,
    FailureSource,
    FailureWorklist,
    ResolutionCommit,
)
from .tools import (
    READ_WORKLIST_TOOL_NAME,
    WRITE_WORKLIST_TOOL_NAME,
    register_worklist_tools,
)
from .worklist import FailureWorklistStore


_MODEL_ROLE = "operation_smoke_failure_resolution"
_COMPACT_TRIGGER_RATIO = 0.80
_COMPACT_SUMMARY_MAX_CHARS = 24_000
_COMPACT_SUMMARY_PREFIX = """Another Failure Resolution model previously investigated this operation.
Continue from this checkpoint, verify uncertain facts through the worklist
and reference tools, and avoid repeating completed investigation:"""
_MEMORY_TOOL_NAME = "lookup_parameter_history"
_PATCH_TOOL_NAME = "generate_parameter_patch"
_READ_ONLY_TOOL_NAMES = frozenset(
    {
        READ_WORKLIST_TOOL_NAME,
        READ_CANDIDATE_TOOL_NAME,
        OPENAPI_LIST_INPUTS_TOOL_NAME,
        OPENAPI_LIST_RESPONSE_FIELDS_TOOL_NAME,
        OPENAPI_GET_INPUT_SCHEMA_TOOL_NAME,
        OPENAPI_GET_RESPONSE_FIELD_SCHEMA_TOOL_NAME,
        *TEST_CASE_TOOL_NAMES,
        _MEMORY_TOOL_NAME,
    }
)


class HTTPProbe(Protocol):
    """Describe the HTTP capability restricted to the current operation."""

    def tool_spec(self, config: OperationGeneratorConfig):
        """Return the current-operation-scoped model tool contract."""
        ...

    def validate(self, *, config: OperationGeneratorConfig, tool_call) -> str | None:
        """Return a scope error without sending a target request."""
        ...

    def execute(self, *, config: OperationGeneratorConfig, tool_call, catalog):
        """Execute and record one previously validated probe attempt."""
        ...


class ResolutionMemory(Protocol):
    """Expose only read-only Parameter history to Failure Resolution Agent."""

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Read prior attributed conclusions for one exact operation input."""
        ...


class PatchCoordinatorFactory(Protocol):
    """Create a fresh Parameter Patch Coordinator for each Agent request."""

    def create(self) -> ParameterPatchCoordinator:
        """Return an isolated Patch/Review conversation."""
        ...


class ResolutionFinalizer(Protocol):
    """Mechanically validate and atomically persist one final worklist."""

    def finalize(
        self,
        *,
        request: FailureResolutionRequest,
        sources: tuple[FailureSource, ...],
        worklist: FailureWorklist,
        candidates: PatchCandidateRegistry,
        catalog: TestCaseCatalog,
        current: OperationGeneratorConfig | None = None,
        active_constraints: list[CompiledConstraintPatch] | None = None,
        prepare_patch_updates: Callable | None = None,
        validate_combined_patch: Callable[[GeneratorPatchDraft], None] | None = None,
    ) -> ResolutionCommit:
        """Return a commit summary or raise a model-correctable ToolFailure."""
        ...


class FailureResolutionAgent:
    """Create one continuous LLM session for all Failures in a failed Batch."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        compact_model: LLMModelConfig,
        openapi_capability: OpenAPICapability,
        finalizer: ResolutionFinalizer,
        http_probe: HTTPProbe | None = None,
        memory: ResolutionMemory | None = None,
        patch_coordinator_factory: PatchCoordinatorFactory | None = None,
        reference_values: ReferenceValueProvider | None = None,
        system_prompt: str | None = None,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store stateless collaborators shared by fresh Resolution sessions."""
        self.client = client
        self.model = model
        self.compact_agent = FailureResolutionCompactAgent(
            client=client,
            model=compact_model,
            tracing_runtime=tracing_runtime,
        )
        self.openapi_capability = openapi_capability
        self.finalizer = finalizer
        self.http_probe = http_probe
        self.memory = memory
        self.patch_coordinator_factory = patch_coordinator_factory
        self.reference_values = reference_values
        self.system_prompt = system_prompt or failure_resolution_system_prompt()
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def start(
        self,
        request: FailureResolutionRequest,
        *,
        catalog: TestCaseCatalog,
        output_limit: ModelOutputLimit,
        config: OperationGeneratorConfig | None = None,
        active_constraints: list[CompiledConstraintPatch] | None = None,
        case_count: int = 10,
        random_seed: int = 0,
        prepare_patch_updates: Callable | None = None,
        validate_combined_patch: Callable[[GeneratorPatchDraft], None] | None = None,
    ) -> "FailureResolutionSession":
        """Build a session from exact Catalog failures without calling a model."""
        if not self.model.enabled:
            raise RuntimeError("The Failure Resolution model is not configured")
        sources = build_failure_sources(catalog=catalog, case_ids=request.case_ids)
        if not sources:
            raise ValueError("Failure Resolution requires at least one Failure message")
        return FailureResolutionSession(
            client=self.client,
            model=self.model,
            compact_agent=self.compact_agent,
            openapi_capability=self.openapi_capability,
            finalizer=self.finalizer,
            http_probe=self.http_probe,
            memory=self.memory,
            patch_coordinator_factory=self.patch_coordinator_factory,
            reference_values=self.reference_values,
            system_prompt=self.system_prompt,
            validator=self.validator,
            tracing_runtime=self.tracing_runtime,
            request=request,
            catalog=catalog,
            sources=sources,
            output_limit=output_limit,
            config=config,
            active_constraints=active_constraints or [],
            case_count=case_count,
            random_seed=random_seed,
            prepare_patch_updates=prepare_patch_updates,
            validate_combined_patch=validate_combined_patch,
        )


class FailureResolutionSession:
    """Retain worklist, registries, tools, context, and per-item round counts."""

    def __init__(
        self,
        *,
        client: LLMClient,
        model: LLMModelConfig,
        compact_agent: FailureResolutionCompactAgent,
        openapi_capability: OpenAPICapability,
        finalizer: ResolutionFinalizer,
        http_probe: HTTPProbe | None,
        memory: ResolutionMemory | None,
        patch_coordinator_factory: PatchCoordinatorFactory | None,
        reference_values: ReferenceValueProvider | None,
        system_prompt: str,
        validator: OutputValidator,
        tracing_runtime: TracingRuntime,
        request: FailureResolutionRequest,
        catalog: TestCaseCatalog,
        sources: list[FailureSource],
        output_limit: ModelOutputLimit,
        config: OperationGeneratorConfig | None,
        active_constraints: list[CompiledConstraintPatch],
        case_count: int,
        random_seed: int,
        prepare_patch_updates: Callable | None,
        validate_combined_patch: Callable[[GeneratorPatchDraft], None] | None,
    ) -> None:
        """Create all run-local state without persisting or querying extra facts."""
        self.client = client
        self.model = model
        self.compact_agent = compact_agent
        self.openapi_capability = openapi_capability
        self.finalizer = finalizer
        self.http_probe = http_probe
        self.memory = memory
        self.patch_coordinator_factory = patch_coordinator_factory
        self.reference_values = reference_values
        self.validator = validator
        self.tracing_runtime = tracing_runtime
        self.request = request
        self.catalog = catalog
        self.output_limit = output_limit
        self.config = config
        self.active_constraints = list(active_constraints)
        self.case_count = case_count
        self.random_seed = random_seed
        self.prepare_patch_updates = prepare_patch_updates
        self.validate_combined_patch = validate_combined_patch
        self.semantic_inputs = (
            build_semantic_input_map(config) if config is not None else None
        )
        self.queried_parameter_handles: set[str] = set()
        self.parameter_history_for_patch: list[dict[str, Any]] = []
        self.starting_output_count = output_limit.used
        self.candidates = PatchCandidateRegistry()
        self.worklist = FailureWorklistStore(
            sources=sources,
            valid_parameters=catalog.valid_parameters,
            candidate_refs=self.candidates.refs,
        )
        self.active_item_outputs: dict[str, int] = {}
        self.compaction_disabled = False
        self.group_count_after_compact = 0
        self.last_resolution_prompt_tokens: int | None = None
        self.last_resolution_history_bytes: int | None = None
        self.tools = self._build_tools()
        self.model_tools = _uniform_model_tool_specs(self.tools.specs())
        rendered = failure_source_prompt(
            operation_key=request.operation_key,
            sources=sources,
        )
        self.context = AgentContext(
            system=system_prompt,
            user=rendered.text,
            limits=ContextLimits(
                system_chars=6_000,
                initial_user_chars=24_000,
                feedback_chars=8_000,
                conversation_chars=(
                    model.context_window_tokens - model.max_tokens
                )
                * 4,
                required_output_tokens=model.max_tokens,
            ),
            metrics=rendered.metrics,
        )

    def advance(self) -> FailureResolutionOutcome:
        """Continue until finalization succeeds or the shared hard limit stops it."""
        with self.tracing_runtime.span(
            "FailureResolutionAgent.resolve",
            kind="AGENT",
            input_value={
                "operation_key": self.request.operation_key,
                "source_count": len(self.worklist.sources),
            },
        ) as span:
            while True:
                active_item_id = self.worklist.read().active_item_id
                try:
                    self._compact_context_if_needed()
                    self.output_limit.consume(_MODEL_ROLE)
                except ModelOutputLimitExceeded as exc:
                    outcome = FailureResolutionOutcome(
                        status="failure_resolution_limit_exceeded",
                        outputs_used=self._outputs_used(),
                        source_count=len(self.worklist.sources),
                        worklist=self.worklist.read(),
                        reason=str(exc),
                    )
                    span.set_output(outcome.model_dump(mode="json"))
                    return outcome
                request = self._request()
                self.last_resolution_history_bytes = _history_bytes(
                    self.context.clone_history()
                )
                response = self.client.invoke(request)
                self.last_resolution_prompt_tokens = response.prompt_tokens
                item_round = self._record_active_item_output(active_item_id)
                if response.tool_calls:
                    errors = self._tool_errors(response)
                    if errors:
                        self._append_correction(response, errors)
                    else:
                        try:
                            self._execute_tools(response)
                        except ModelOutputLimitExceeded as exc:
                            outcome = FailureResolutionOutcome(
                                status="failure_resolution_limit_exceeded",
                                outputs_used=self._outputs_used(),
                                source_count=len(self.worklist.sources),
                                worklist=self.worklist.read(),
                                reason=str(exc),
                            )
                            span.set_output(outcome.model_dump(mode="json"))
                            return outcome
                    self._append_progress_warning(active_item_id, item_round)
                    continue

                finish, errors = self._finish(response)
                if finish is None or errors:
                    self._append_correction(
                        response,
                        errors or ["The Resolution output could not be used."],
                    )
                    self._append_progress_warning(active_item_id, item_round)
                    continue
                try:
                    self.worklist.require_complete_coverage()
                    commit = self.finalizer.finalize(
                        request=self.request,
                        sources=self.worklist.sources,
                        worklist=self.worklist.read(),
                        candidates=self.candidates,
                        catalog=self.catalog,
                        current=self.config,
                        active_constraints=self.active_constraints,
                        prepare_patch_updates=self.prepare_patch_updates,
                        validate_combined_patch=self.validate_combined_patch,
                    )
                except ToolFailure as exc:
                    self.context.append_assistant(response)
                    self.context.append_feedback(
                        "FINAL WORKLIST REJECTED\n"
                        f"issue | {exc.safe_message}\n"
                        "Read or rewrite the worklist, then decide when to finish again."
                    )
                    self._append_progress_warning(active_item_id, item_round)
                    continue

                outcome = FailureResolutionOutcome(
                    status="completed",
                    outputs_used=self._outputs_used(),
                    source_count=len(self.worklist.sources),
                    worklist=self.worklist.read(),
                    commit=commit,
                    reason=finish.reason,
                )
                span.set_output(
                    {
                        "status": outcome.status,
                        "outputs_used": outcome.outputs_used,
                        "attempt_count": len(commit.attempt_ids),
                        "applied_candidate_count": len(commit.applied_candidate_refs),
                    }
                )
                return outcome

    def _compact_context_if_needed(self) -> None:
        """Replace H with U plus S once the next Resolution prompt reaches 80%.

        A failed two-attempt Compact run leaves B and H untouched and disables
        semantic compaction for the remainder of this Resolution session. The
        existing mechanical projection remains the final provider-window guard.

        Raises:
            ModelOutputLimitExceeded: When a Compact attempt cannot reserve one
                of the Operation-wide model outputs.
        """
        if self.compaction_disabled:
            return
        if self.context.metrics.conversation_group_count <= self.group_count_after_compact:
            return
        if self._estimated_next_prompt_tokens() < self._compact_trigger_tokens():
            return

        try:
            compact_summary = self.compact_agent.run(
                context=self.context,
                output_limit=self.output_limit,
            )
        except FailureResolutionCompactError:
            self.compaction_disabled = True
            return

        bounded_summary = _clip_compact_summary(
            compact_summary,
            max_chars=_COMPACT_SUMMARY_MAX_CHARS,
        )
        history_after_compact = (
            _COMPACT_SUMMARY_PREFIX + "\n\n" + bounded_summary
        )
        self.context.replace_compacted_history(
            history_after_compact,
            max_summary_chars=(
                len(_COMPACT_SUMMARY_PREFIX)
                + 2
                + _COMPACT_SUMMARY_MAX_CHARS
            ),
        )
        self.group_count_after_compact = (
            self.context.metrics.conversation_group_count
        )
        # The next Resolution response establishes a fresh provider-reported
        # token baseline for the newly compacted history.
        self.last_resolution_prompt_tokens = None
        self.last_resolution_history_bytes = None

    def _compact_trigger_tokens(self) -> int:
        """Return 80% of the configured Resolution input capacity."""
        usable_input_tokens = (
            self.model.context_window_tokens - self.model.max_tokens
        )
        return int(usable_input_tokens * _COMPACT_TRIGGER_RATIO)

    def _estimated_next_prompt_tokens(self) -> int:
        """Prefer provider usage and conservatively include newly saved H bytes."""
        current_history_bytes = _history_bytes(self.context.clone_history())
        if (
            self.last_resolution_prompt_tokens is not None
            and self.last_resolution_history_bytes is not None
        ):
            new_history_bytes = max(
                0,
                current_history_bytes - self.last_resolution_history_bytes,
            )
            return self.last_resolution_prompt_tokens + new_history_bytes

        # Some compatible providers omit token usage. Counting serialized UTF-8
        # bytes for the complete request, including tools and output Schema, is
        # intentionally conservative because one token cannot represent less
        # than one byte of provider input.
        return len(self._request().model_dump_json().encode("utf-8"))

    def _request(self) -> LLMRequest:
        """Build one provider request for the unified Resolution model role."""
        return LLMRequest(
            provider=self.model.provider,
            model=self.model.model,
            messages=self.context.messages_for_request(self.model),
            temperature=0,
            max_tokens=self.model.max_tokens,
            response_format="json_schema",
            json_schema=FailureResolutionFinish.model_json_schema(),
            json_schema_name="FailureResolutionFinish",
            tools=self.model_tools,
            tool_choice="auto",
            timeout_seconds=self.model.timeout_seconds,
            reasoning=self.model.reasoning,
            metadata={"role": _MODEL_ROLE},
        )

    def _build_tools(self) -> AgentToolbox:
        """Bind reference state and bounded OpenAPI/Test Case lookups."""
        toolbox = AgentToolbox(tracing_runtime=self.tracing_runtime)
        toolbox.register(
            spec=openapi_list_inputs_tool_spec(),
            execute=self.openapi_capability.list_inputs,
        )
        toolbox.register(
            spec=openapi_list_response_fields_tool_spec(),
            execute=self.openapi_capability.list_response_fields,
        )
        toolbox.register(
            spec=openapi_get_input_schema_tool_spec(),
            execute=self.openapi_capability.get_input_schema,
        )
        toolbox.register(
            spec=openapi_get_response_field_schema_tool_spec(),
            execute=self.openapi_capability.get_response_field_schema,
        )
        register_test_case_tools(toolbox=toolbox, catalog=self.catalog)
        register_worklist_tools(toolbox=toolbox, store=self.worklist)
        register_candidate_read_tool(toolbox=toolbox, registry=self.candidates)
        if self.config is not None:
            if self.memory is not None:
                toolbox.register(
                    spec=_parameter_memory_tool_spec(),
                    execute=lambda *, input_handles: self._read_parameter_history(
                        list(input_handles)
                    ),
                )
            if self.patch_coordinator_factory is not None:
                toolbox.register(
                    spec=_patch_tool_spec(),
                    # The session intercepts this stateful tool so the shared
                    # output-limit exception cannot be converted to a generic
                    # toolbox failure.
                    execute=lambda **_arguments: {},
                )
            if self.http_probe is not None:
                toolbox.register(
                    spec=self.http_probe.tool_spec(self.config),
                    # HTTP is likewise executed by the session so every repeat
                    # becomes a fresh request and a fresh TC reference.
                    execute=lambda **_arguments: {},
                )
        return toolbox

    def _tool_errors(self, response: LLMResponse) -> list[str]:
        """Reject an unsafe whole output before any stateful tool can execute."""
        errors: list[str] = []
        if response.parsed_json is not None or (
            response.content is not None and response.content.strip()
        ):
            errors.append("Do not mix tool calls with a finish decision.")
        call_ids = [call.id for call in response.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            errors.append("Every Resolution tool call must have a unique call id.")
        if len(response.tool_calls) > 1:
            names = {call.name for call in response.tool_calls}
            if WRITE_WORKLIST_TOOL_NAME in names:
                errors.append("A worklist write must be the only tool call in one output.")
            elif any(name not in _READ_ONLY_TOOL_NAMES for name in names):
                errors.append(
                    "Only read-only evidence tools may be grouped in one output."
                )
        if errors:
            return errors
        for call in response.tool_calls:
            if call.name == _PATCH_TOOL_NAME:
                errors.extend(self._patch_tool_errors(call.arguments))
            elif call.name == HTTP_REQUEST_TOOL_NAME:
                if self.config is None or self.http_probe is None:
                    errors.append("The current-operation HTTP probe is unavailable.")
                elif self.worklist.read().active_item_id is None:
                    # A probe may mutate the target. Requiring an active item
                    # gives the action an explicit investigation owner and
                    # ensures its model outputs participate in round nudges.
                    errors.append("Set active_item_id before using the HTTP probe.")
                else:
                    error = self.http_probe.validate(
                        config=self.config,
                        tool_call=call,
                    )
                    if error is not None:
                        errors.append(error)
        return errors

    def _execute_tools(self, response: LLMResponse) -> None:
        """Execute a fully validated group and append one result per call."""
        self.context.append_assistant(response)
        if len(response.tool_calls) == 1 and response.tool_calls[0].name == _PATCH_TOOL_NAME:
            results = [self._execute_patch_tool(response.tool_calls[0])]
        elif (
            len(response.tool_calls) == 1
            and response.tool_calls[0].name == HTTP_REQUEST_TOOL_NAME
        ):
            assert self.config is not None and self.http_probe is not None
            result = self.http_probe.execute(
                config=self.config,
                tool_call=response.tool_calls[0],
                catalog=self.catalog,
            )
            self._associate_probe_case(result)
            results = [result]
        else:
            results = (
                self.tools.execute_many(response.tool_calls)
                if len(response.tool_calls) > 1
                else [self.tools.execute(response.tool_calls[0])]
            )
            self._remember_parameter_history(response.tool_calls, results)
        for result in results:
            self.context.append_tool_result(
                result.name,
                result.tool_call_id,
                (
                    tool_result_json(result)
                    if result.name in TEST_CASE_TOOL_NAMES | {HTTP_REQUEST_TOOL_NAME}
                    else _tool_result_text(result)
                ),
            )

    def _associate_probe_case(self, result: ToolResult) -> None:
        """Make an exact repeated Probe Failure available as optional E evidence."""
        structured = result.structured
        if not isinstance(structured, dict):
            return
        case_ref = structured.get("case_id")
        if not isinstance(case_ref, str):
            return
        case = self.catalog.get_case(case_ref)
        if case.failure is None:
            return
        self.worklist.associate_probe_case(
            case_ref=case_ref,
            failure_messages=case.failure.messages,
        )

    def _finish(
        self,
        response: LLMResponse,
    ) -> tuple[FailureResolutionFinish | None, list[str]]:
        """Validate one non-tool model output as the sole finish DTO."""
        validation = self.validator.validate(
            response=response,
            output_model=FailureResolutionFinish,
        )
        if validation.valid:
            return (
                FailureResolutionFinish.model_validate(validation.validated_object),
                [],
            )
        return None, [
            (
                f"{issue.location}: {issue.message}"
                if issue.location
                else issue.message
            )
            for issue in validation.errors
        ]

    def _patch_tool_errors(self, arguments: dict[str, Any]) -> list[str]:
        """Validate one Patch request and its active-item evidence prerequisites."""
        if self.config is None or self.semantic_inputs is None:
            return ["Parameter Patch is unavailable without current Generator state."]
        active_item_id = self.worklist.read().active_item_id
        if active_item_id is None:
            return ["Set active_item_id before requesting a Patch candidate."]
        required_text = ("root_cause", "value_requirements")
        errors = [
            f"{name} must be a non-empty string."
            for name in required_text
            if not isinstance(arguments.get(name), str)
            or not arguments[name].strip()
        ]
        affected = arguments.get("affected_inputs")
        if (
            not isinstance(affected, list)
            or not affected
            or any(not isinstance(value, str) for value in affected)
        ):
            errors.append("affected_inputs must be a non-empty string array.")
            affected = []
        elif len(affected) != len(set(affected)):
            errors.append("affected_inputs must be unique.")
        unknown = sorted(
            set(affected) - set(self.semantic_inputs.node_by_handle)
        )
        if unknown:
            errors.append("Unknown semantic input: " + ", ".join(unknown))
        criteria = arguments.get("acceptance_criteria")
        if (
            not isinstance(criteria, list)
            or not 1 <= len(criteria) <= 20
            or any(not isinstance(value, str) or not value.strip() for value in criteria)
        ):
            errors.append(
                "acceptance_criteria must contain 1 to 20 non-empty strings."
            )
        elif len(criteria) != len(set(criteria)):
            errors.append("acceptance_criteria must be unique.")
        missing_history = sorted(set(affected) - self.queried_parameter_handles)
        if missing_history:
            errors.append(
                "Query Parameter memory before generating a Patch for: "
                + ", ".join(missing_history)
            )
        return errors

    def _read_parameter_history(self, handles: list[str]) -> dict[str, Any]:
        """Read one exact Parameter history and remove storage identities."""
        if self.memory is None or self.semantic_inputs is None or self.config is None:
            raise ToolFailure(
                code="parameter_memory_unavailable",
                message="Parameter Memory is unavailable in this session.",
            )
        if len(handles) != 1:
            raise ToolFailure(
                code="invalid_parameter_history_query",
                message="Read exactly one Parameter handle per Memory tool call.",
            )
        handle = handles[0]
        node_id = self.semantic_inputs.node_by_handle.get(handle)
        if node_id is None:
            raise ToolFailure(
                code="unknown_parameter",
                message=f"Unknown Parameter: {handle}",
            )
        history = self.memory.parameter_history(
            operation_key=self.request.operation_key,
            input_node_id=node_id,
        )
        rendered = _parameter_history_text(
            handle=handle,
            history=history,
            config=self.config,
            handle_by_node=self.semantic_inputs.handle_by_node,
        )
        return {
            "content": rendered,
            "structured": {
                "parameters": [
                    _parameter_history_for_prompt(
                        handle=handle,
                        history=history,
                        handle_by_node=self.semantic_inputs.handle_by_node,
                    )
                ]
            },
        }

    def _remember_parameter_history(
        self,
        calls: list[ToolCall],
        results: list[ToolResult],
    ) -> None:
        """Retain successful read provenance for later Patch requirements."""
        for call, result in zip(calls, results, strict=True):
            if call.name != _MEMORY_TOOL_NAME or result.status != "succeeded":
                continue
            handles = call.arguments.get("input_handles")
            if not isinstance(handles, list):
                continue
            structured = result.structured
            if not isinstance(structured, dict):
                continue
            parameters = structured.get("parameters")
            if not isinstance(parameters, list):
                continue
            self.queried_parameter_handles.update(handles)
            self.parameter_history_for_patch.extend(parameters)

    def _execute_patch_tool(self, call: ToolCall) -> ToolResult:
        """Run Patch/Review under the shared guard and issue one opaque P ref."""
        assert self.config is not None
        assert self.semantic_inputs is not None
        assert self.patch_coordinator_factory is not None
        snapshot = self.worklist.read()
        active = next(
            item for item in snapshot.items if item.item_id == snapshot.active_item_id
        )
        task = ParameterPatchTask(
            todo_id=active.item_id,
            failure="; ".join(
                source.message
                for source in self.worklist.sources
                if source.failure_ref in active.source_failure_refs
            ),
            root_cause=call.arguments["root_cause"],
            affected_inputs=list(call.arguments["affected_inputs"]),
            value_requirements=call.arguments["value_requirements"],
            acceptance_criteria=list(call.arguments["acceptance_criteria"]),
            prior_attempts=list(self.parameter_history_for_patch),
        )
        result = self.patch_coordinator_factory.create().run(
            task=task,
            config=self.config,
            active_constraints=self.active_constraints,
            case_count=self.case_count,
            reference_values=self.reference_values,
            random_seed=self.random_seed,
            output_limit=self.output_limit,
        )
        preview = preview_generator_patch(self.config, result.patch.updates)
        candidate = self.candidates.issue(
            patch=result.patch,
            root_cause=task.root_cause,
            change_reason=task.value_requirements,
            affected_parameters=task.affected_inputs,
            parameter_attributions=[
                SolveAttemptParameterWrite(
                    input_node_id=self.semantic_inputs.node_by_handle[handle],
                    cause_summary=task.root_cause,
                )
                for handle in task.affected_inputs
            ],
            before_generators=_generator_summary(
                self.config,
                affected_inputs=task.affected_inputs,
            ),
            after_generators=_generator_summary(
                preview,
                affected_inputs=task.affected_inputs,
            ),
            samples=result.samples,
            outputs_used=result.outputs_used,
        )
        summary = self.candidates.summary(candidate.candidate_ref)
        return ToolResult(
            tool_call_id=call.id,
            name=call.name,
            status="succeeded",
            content=_candidate_summary_text(summary),
            structured=summary.model_dump(mode="json"),
        )

    def _append_correction(self, response: LLMResponse, errors: list[str]) -> None:
        """Return validation problems without retaining unexecuted tool calls."""
        if not response.tool_calls:
            self.context.append_assistant(response)
        self.context.append_feedback(
            "RESOLUTION OUTPUT INVALID\n"
            + "\n".join(f"issue | {error}" for error in errors)
            + "\nUse read-only evidence calls, one stateful tool, or one finish JSON."
        )

    def _record_active_item_output(self, item_id: str | None) -> int:
        """Increment only the item active when this model output was requested."""
        if item_id is None:
            return 0
        value = self.active_item_outputs.get(item_id, 0) + 1
        self.active_item_outputs[item_id] = value
        return value

    def _append_progress_warning(self, item_id: str | None, item_round: int) -> None:
        """Add urgency from the eleventh non-terminal output for one active item."""
        if item_id is None or item_round < 11:
            return
        self.context.append_feedback(
            f"当前 Failure 已处理 {item_round} 轮；本次 Operation 最多允许 "
            f"{self.output_limit.max_outputs} 个模型输出，当前剩余 "
            f"{self.output_limit.remaining}。预算有限。如果现有证据不足以支持安全 "
            "Patch，请尽快记录 no_patch；否则只执行形成或选择 Patch 所必需的下一步。"
        )

    def _outputs_used(self) -> int:
        """Report only calls consumed since this Resolution session started."""
        return self.output_limit.used - self.starting_output_count


def build_failure_sources(
    *,
    catalog: TestCaseCatalog,
    case_ids: list[str],
) -> list[FailureSource]:
    """Fold exact duplicate messages while preserving every E-to-TC association."""
    cases_by_message: OrderedDict[str, list[str]] = OrderedDict()
    for case_id in case_ids:
        case = catalog.get_case(case_id)
        if case.failure is None:
            continue
        for message in case.failure.messages:
            associated = cases_by_message.setdefault(message, [])
            if case_id not in associated:
                associated.append(case_id)
    if len(cases_by_message) > 100:
        raise ValueError("Failure Resolution supports at most 100 exact messages")
    return [
        FailureSource(
            failure_ref=f"E{index}",
            message=message,
            test_case_refs=associated,
        )
        for index, (message, associated) in enumerate(
            cases_by_message.items(),
            start=1,
        )
    ]


def _history_bytes(messages: list[LLMMessage]) -> int:
    """Return a conservative byte count for newly saved provider messages."""
    payload = [message.model_dump(mode="json") for message in messages]
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _clip_compact_summary(summary: str, *, max_chars: int) -> str:
    """Bound only summary ``S`` while retaining evidence from both ends.

    Args:
        summary: Markdown returned by the Compact Agent.
        max_chars: Maximum characters that may enter the saved handoff.

    Returns:
        The original Markdown when it fits, otherwise an exact-size visible
        clipping marker followed by the summary's head and tail. The caller's
        fixed handoff prefix is deliberately not part of this transformation.

    Raises:
        ValueError: If the requested character allowance is not positive.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    if len(summary) <= max_chars:
        return summary
    marker = f"CLIPPED COMPACT SUMMARY | original-chars={len(summary)}\n"
    if len(marker) >= max_chars:
        return marker[:max_chars]
    remaining = max_chars - len(marker)
    separator = "\n...\n"
    if remaining <= len(separator) + 2:
        return (marker + summary[:remaining])[:max_chars]
    evidence_chars = remaining - len(separator)
    head_chars = evidence_chars // 2
    tail_chars = evidence_chars - head_chars
    return (
        marker
        + summary[:head_chars]
        + separator
        + summary[-tail_chars:]
    )


def _uniform_model_tool_specs(specs: list[ToolSpec]) -> list[ToolSpec]:
    """Avoid provider-invalid mixtures while preserving local validation.

    Some providers reject one request that contains both strict and non-strict
    functions. Resolution's operation-scoped HTTP Probe intentionally accepts
    flexible JSON bodies and therefore cannot safely promise provider-side
    strict Schema enforcement. When any offered tool has that property, every
    model-facing copy becomes non-strict for a uniform request. The original
    toolbox specifications remain unchanged, so RESTScope still validates all
    arguments locally before executing a read, write, Patch, or HTTP action.
    """
    if all(spec.strict for spec in specs):
        return specs
    return [spec.model_copy(update={"strict": False}) for spec in specs]


def _tool_result_text(result: ToolResult) -> str:
    """Render non-Test-Case runtime output as bounded Markdown, not prompt JSON."""
    writer = CompactTextWriter(max_value_chars=1_200)
    writer.section("TOOL RESULT", untrusted=True)
    error = result.error if isinstance(result.error, dict) else {}
    writer.record(
        result.name,
        status=result.status,
        error_code=error.get("code"),
        error_message=error.get("message"),
    )
    if result.structured is not None:
        writer.detail("result", result.structured)
    if result.content:
        writer.text("body", result.content)
    return writer.render(max_chars=8_000).text


def _parameter_memory_tool_spec() -> ToolSpec:
    """Describe a one-handle read without preloading operation Parameters."""
    return ToolSpec(
        name=_MEMORY_TOOL_NAME,
        description=(
            "Read prior attributed Failures, root causes, conflicts, and applied "
            "changes for exactly one semantic Parameter handle discovered from "
            "OpenAPI or Test Case evidence."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "input_handles": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 1,
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
        strict=True,
    )


def _patch_tool_spec() -> ToolSpec:
    """Describe side-effect-free candidate construction using semantic handles."""
    return ToolSpec(
        name=_PATCH_TOOL_NAME,
        description=(
            "Build, compile, sample, and independently review one Generator or "
            "Constraint candidate for the active worklist item. Returns only a "
            "new P* reference and bounded summary; it does not apply the Patch."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "root_cause": {"type": "string", "minLength": 1},
                "affected_inputs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 100,
                    "uniqueItems": True,
                },
                "value_requirements": {"type": "string", "minLength": 1},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 20,
                    "uniqueItems": True,
                },
            },
            "required": [
                "root_cause",
                "affected_inputs",
                "value_requirements",
                "acceptance_criteria",
            ],
            "additionalProperties": False,
        },
        output_schema=PatchCandidateSummary.model_json_schema(),
        strict=True,
    )


def _parameter_history_text(
    *,
    handle: str,
    history: ParameterHistory,
    config: OperationGeneratorConfig,
    handle_by_node,
) -> str:
    """Render prior conclusions and current Generator state as bounded Markdown."""
    writer = CompactTextWriter(max_value_chars=800)
    writer.section(f"PREVIOUS RESULTS FOR INPUT {handle}", untrusted=True)
    current = next(
        (
            item
            for item in config.configs
            if item.input_node_id == history.input_node_id
        ),
        None,
    )
    writer.record(
        "current",
        required=False,
        generator=(
            current.strategy.model_dump(mode="python") if current is not None else None
        ),
        inclusion_probability=(
            current.inclusion_probability if current is not None else None
        ),
        related_failures=len(history.failures),
    )
    for failure in history.failures:
        writer.text("failure", failure.summary)
        for attempt in failure.attempts:
            writer.record(
                f"round-{attempt.round_number}",
                outcome=attempt.outcome,
                cause=attempt.root_cause,
                reason=attempt.reason,
                parameters=[
                    handle_by_node.get(parameter.input_node_id, "<inactive-input>")
                    for parameter in attempt.parameters
                ],
                generator_change_event=(
                    attempt.generator_change.event_id
                    if attempt.generator_change is not None
                    else None
                ),
            )
    return writer.render(max_chars=8_000).text


def _parameter_history_for_prompt(
    *,
    handle: str,
    history: ParameterHistory,
    handle_by_node,
) -> dict[str, Any]:
    """Remove persistence identities from one model-facing Memory projection."""
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


def _generator_summary(
    config: OperationGeneratorConfig,
    *,
    affected_inputs: list[str],
) -> dict[str, dict]:
    """Project exact Generator state under model-visible semantic handles."""
    semantic = build_semantic_input_map(config)
    by_node = {item.input_node_id: item for item in config.configs}
    output: dict[str, dict] = {}
    for handle in affected_inputs:
        summary = by_node[semantic.node_by_handle[handle]].model_dump(mode="json")
        strategy = summary.get("strategy")
        if isinstance(strategy, dict) and strategy.get("type") == "response_value":
            summary["strategy"] = {"type": "response_value"}
        output[handle] = summary
    return output


def _candidate_summary_text(summary: PatchCandidateSummary) -> str:
    """Render the same non-executable candidate summary returned as structured data."""
    writer = CompactTextWriter(max_value_chars=1_200)
    writer.section(
        f"VALIDATED PATCH CANDIDATE {summary.candidate_ref}",
        untrusted=True,
    )
    writer.record(
        "candidate",
        validation_status=summary.validation_status,
        root_cause=summary.root_cause,
        affected_parameters=summary.affected_parameters,
        generator_changes=summary.generator_change_overview,
        constraint_changes=summary.constraint_change_overview,
        sample_count=summary.sample_overview.sample_count,
        sample_coverage=summary.sample_overview.covered_parameters,
        model_outputs_used=summary.model_outputs_used,
    )
    return writer.render(max_chars=8_000).text
