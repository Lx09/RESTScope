"""Behavioral contracts for memory- and Patch-tool-driven Failure Solve."""

from __future__ import annotations

from restscope.llm import (
    LLMModelConfig,
    LLMResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.operation_smoke.memory import (
    AppliedSmokePatch,
    FailureHistory,
    ParameterHistory,
)
from restscope.operation_smoke.parameter_patch import (
    GeneratorPatchDraft,
    ParameterPatchFailure,
    ValidatedParameterPatch,
)
from restscope.testing import InputGeneratorPatch
from restscope.testing.models import ConstantGenerator

from tests._operation_smoke_dedup_solve_fixtures import smoke_config


class StubClient:
    """Return scripted Solve outputs and retain each request for inspection."""

    def __init__(self, responses: list[LLMResponse | dict]) -> None:
        self.responses = [
            response
            if isinstance(response, LLMResponse)
            else LLMResponse(
                provider="stub",
                model="think-model",
                parsed_json=response,
            )
            for response in responses
        ]
        self.requests = []

    def invoke(self, request):
        """Return the next scripted response."""
        self.requests.append(request)
        return self.responses.pop(0)


class ToolContractAwareClient:
    """Model the shortest Solve path only when its tool contract is discoverable.

    This deterministic client stands in for the behavior seen in Phoenix Evals.
    It does not know RESTScope's private semantic-handle convention. It can
    choose one valid tool at a time only when the system instruction states that
    protocol and each Parameter tool schema lists the accepted handles.
    """

    def __init__(self) -> None:
        """Start with no requests or tool-contract knowledge."""
        self.requests = []

    def invoke(self, request):
        """Choose the next tool from information visible in the LLM request."""
        self.requests.append(request)
        system_prompt = request.messages[0].content
        tools_by_name = {tool.name: tool for tool in request.tools}
        memory_results = [
            message
            for message in request.messages
            if message.role == "tool"
            and message.name == "lookup_parameter_history"
        ]
        patch_results = [
            message
            for message in request.messages
            if message.role == "tool"
            and message.name == "generate_parameter_patch"
        ]

        if patch_results:
            return LLMResponse(
                provider="stub",
                model="think-model",
                parsed_json=_terminal("apply_patch", candidate_ref="P1"),
            )

        if memory_results:
            patch_schema = tools_by_name["generate_parameter_patch"].input_schema
            allowed_handles = patch_schema["properties"]["affected_inputs"][
                "items"
            ].get("enum")
            handle = (
                allowed_handles[0]
                if allowed_handles
                else "path/projectId"
            )
            return _patch_call_with_handle(handle)

        memory_schema = tools_by_name["lookup_parameter_history"].input_schema
        allowed_handles = memory_schema["properties"]["input_handles"][
            "items"
        ].get("enum")
        if "exactly one tool" in system_prompt.lower() and allowed_handles:
            return _memory_call_with_handle(allowed_handles[0])

        # Phoenix showed DeepSeek repeatedly choosing both independent reads
        # when the one-tool rule and accepted semantic handles were hidden.
        return LLMResponse(
            provider="stub",
            model="think-model",
            tool_calls=[
                ToolCall(
                    id=f"probe-{len(self.requests)}",
                    name="restscope.http.request",
                    arguments={
                        "method": "GET",
                        "path": "/projects/{projectId}",
                        "path_parameters": {"projectId": 5},
                    },
                ),
                ToolCall(
                    id=f"memory-{len(self.requests)}",
                    name="lookup_parameter_history",
                    arguments={"input_handles": ["path/projectId"]},
                ),
            ],
        )


class StubProbe:
    """Provide optional current-operation HTTP evidence without network access."""

    def __init__(self) -> None:
        self.executed = []

    def tool_spec(self, config):
        """Describe the scoped HTTP tool."""
        return ToolSpec(
            name="restscope.http.request",
            description="Probe the current operation.",
            kind="local_function",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

    def validate(self, *, config, tool_call):
        """Accept only the expected HTTP tool name in focused tests."""
        if tool_call.name != "restscope.http.request":
            return f"{tool_call.name} is not the HTTP probe"
        return None

    def execute(self, *, config, tool_call, catalog):
        """Return one bounded status observation."""
        del catalog
        self.executed.append(tool_call.id)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={"status_code": 404},
        )


class StubMemory:
    """Expose current Failure and Parameter histories and record conclusions."""

    def __init__(self) -> None:
        self.failure_lookups = []
        self.parameter_lookups = []
        self.attempts = []

    def failure_history(self, *, operation_key, failure_id):
        """Return the current Failure's complete but empty Solve history."""
        self.failure_lookups.append((operation_key, failure_id))
        return FailureHistory(
            failure_id=failure_id,
            summary="Project identifier is rejected.",
            occurrence_count=1,
        )

    def parameter_history(self, *, operation_key, input_node_id):
        """Return an entry for one resolved operation-local input node."""
        self.parameter_lookups.append((operation_key, input_node_id))
        return ParameterHistory(input_node_id=input_node_id)

    def record_solve_attempt(self, write):
        """Record terminal no-Patch/conflict decisions."""
        self.attempts.append(write)
        return f"solve-attempt-{len(self.attempts)}"


class StubPatchAgent:
    """Return one prepared validated or failed Patch result."""

    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    def run(self, **kwargs):
        """Capture the structured requirement and return the prepared result."""
        self.calls.append(kwargs)
        return self.result


class StubPatchFactory:
    """Create one scripted Patch Agent for each tool invocation."""

    def __init__(self, results) -> None:
        self.results = list(results)
        self.created = []

    def create(self):
        """Return a fresh side-effect-free Patch Agent."""
        agent = StubPatchAgent(self.results.pop(0))
        self.created.append(agent)
        return agent


class StubPatchApplication:
    """Model the one atomic state change after Solve accepts a candidate."""

    def __init__(self) -> None:
        self.calls = []

    def apply(self, **kwargs):
        """Return changed current state and retain the persistence request."""
        from restscope.testing import prepare_accepted_generator_patch

        self.calls.append(kwargs)
        return AppliedSmokePatch(
            config=prepare_accepted_generator_patch(
                kwargs["current"],
                kwargs["patch"].updates,
            ),
            solve_attempt_id="solve-attempt-applied",
            generator_change_event_id="generator-change-applied",
            constraints=(),
        )


def _model() -> LLMModelConfig:
    """Build the THINK model selected for Failure Solve."""
    return LLMModelConfig(
        role="operation_smoke_failure_solve",
        provider="stub",
        model="think-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _request():
    """Build one stable Failure with one representative Catalog reference."""
    from restscope.operation_smoke.failure_solver import FailureSolveRequest

    return FailureSolveRequest(
        operation_key="GET /projects/{projectId}",
        round_number=2,
        todo={
            "todo_id": "T1",
            "failure_id": "db-failure-1",
            "failure": "Project identifier is rejected.",
            "test_case_id": "TC1",
        },
        operation={"method": "GET", "path": "/projects/{projectId}"},
        generator_config={},
    )


def _catalog():
    """Create the shared run-local Catalog visible to one Solve session."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(
        valid_parameters={"path.projectId", "query.region"}
    )
    catalog.record(
        CatalogTestCaseDraft(
            parameters={
                "path.projectId": "missing",
                "query.region": "us-east",
            },
            response_body={"message": "project missing"},
            failure=HTTPFailure(
                status_code=404,
                messages=["HTTP 404: project missing"],
            ),
        )
    )
    return catalog


def _memory_call(call_id: str = "memory-1") -> LLMResponse:
    """Request history for the semantic path input."""
    return _memory_call_with_handle("path.projectId", call_id=call_id)


def _memory_call_with_handle(
    handle: str,
    *,
    call_id: str = "memory-1",
) -> LLMResponse:
    """Request Parameter history using one handle visible to the model."""
    return LLMResponse(
        provider="stub",
        model="think-model",
        tool_calls=[
            ToolCall(
                id=call_id,
                name="lookup_parameter_history",
                arguments={"input_handles": [handle]},
            )
        ],
    )


def _patch_call(
    call_id: str = "patch-1",
    *,
    maximum: int = 100,
) -> LLMResponse:
    """Request an integer-like bounded Generator task from Patch Agent."""
    return _patch_call_with_handle(
        "path.projectId",
        call_id=call_id,
        maximum=maximum,
    )


def _patch_call_with_handle(
    handle: str,
    *,
    call_id: str = "patch-1",
    maximum: int = 100,
) -> LLMResponse:
    """Request a bounded Generator for one model-visible semantic handle."""
    return LLMResponse(
        provider="stub",
        model="think-model",
        tool_calls=[
            ToolCall(
                id=call_id,
                name="generate_parameter_patch",
                arguments={
                    "root_cause": "The unrestricted identifier is rejected.",
                    "affected_inputs": [handle],
                    "desired_behavior": (
                        f"Generate accepted identifiers no greater than {maximum}."
                    ),
                    "acceptance_criteria": (
                        f"Every sample is between 3 and {maximum}."
                    ),
                },
            )
        ],
    )


def _validated_patch(value: str, *, outputs_used: int = 2):
    """Build a locally validated constant candidate for tool-loop tests."""
    return ValidatedParameterPatch(
        todo_id="T1",
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy=ConstantGenerator(type="constant", value=value),
                )
            ]
        ),
        samples=[{"values": {"path.projectId": value}, "present": {"path.projectId": True}}],
        outputs_used=outputs_used,
    )


def _terminal(
    action: str,
    *,
    candidate_ref: str | None = None,
    conflict_reason: str | None = None,
) -> dict:
    """Build the complete durable facts required by a terminal decision."""
    return {
        "action": action,
        "candidate_ref": candidate_ref,
        "trigger_conditions": "Generated identifiers are rejected.",
        "root_cause": "path.projectId uses an unsuitable Generator.",
        "solution": "Use the validated bounded Generator.",
        "evidence_source": "mixed",
        "parameters": [
            {
                "input_handle": "path.projectId",
                "cause_summary": "This input contains the rejected value.",
            }
        ],
        "conflict_reason": conflict_reason,
        "reason": None,
        "next_step": None,
    }


def _agent(client, memory, patch_factory, application):
    """Wire one production Agent with focused deterministic collaborators."""
    from restscope.operation_smoke.failure_solver import FailureSolveAgent

    return FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
        memory=memory,
        patch_coordinator_factory=patch_factory,
        patch_application=application,
        openapi_capability=_openapi_capability(),
    )


def _openapi_capability():
    """Bind Solve's global OpenAPI tools to a trusted in-memory document."""
    from restscope.capabilities import OpenAPICapability, ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Solve", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "projectId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "region",
                                "in": "query",
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {
                            "404": {
                                "description": "missing",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "message": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )
    context = ToolContext(ir=ir, baseline_schema_source={})
    return OpenAPICapability(context_provider=lambda: context)


def _start(agent, **kwargs):
    """Start with the shared seed and empty active Constraint set."""
    return agent.start(
        _request(),
        catalog=_catalog(),
        config=smoke_config(),
        active_constraints=[],
        case_count=2,
        random_seed=731,
        **kwargs,
    )


def test_solve_preloads_failure_queries_parameter_then_atomically_applies_patch() -> None:
    """Scenario: selected candidate changes state only after final Solve output."""
    memory = StubMemory()
    application = StubPatchApplication()
    patch_factory = StubPatchFactory([_validated_patch("known-project")])
    client = StubClient(
        [
            _memory_call(),
            _patch_call(),
            _terminal("apply_patch", candidate_ref="P1"),
        ]
    )

    outcome = _start(
        _agent(client, memory, patch_factory, application)
    ).advance()

    assert outcome.status == "applied_patch"
    assert outcome.outputs_used == 5  # memory + Patch call + 2 Patch outputs + final
    assert outcome.generator_change_event_id == "generator-change-applied"
    assert memory.failure_lookups == [
        ("GET /projects/{projectId}", "db-failure-1")
    ]
    assert memory.parameter_lookups == [
        ("GET /projects/{projectId}", "path/projectId")
    ]
    assert len(application.calls) == 1
    assert application.calls[0]["patch"].updates[0].strategy.value == "known-project"
    assert patch_factory.created[0].calls[0]["random_seed"] == 731
    assert patch_factory.created[0].calls[0]["task"].prior_attempts
    initial_prompt = client.requests[0].messages[1].content
    assert "representative_case=string:\"TC1\"" in initial_prompt
    assert "catalog=string:\"TC1\"" in initial_prompt
    assert "SEMANTIC INPUTS" not in initial_prompt
    assert "path.projectId" not in initial_prompt
    assert "random_string" not in initial_prompt
    assert "missing" not in initial_prompt
    assert "min_access_level" not in initial_prompt
    assert "X-Scenario" not in initial_prompt
    assert "Authorization" not in initial_prompt
    assert "run-2" not in initial_prompt
    assert "current_batch" not in initial_prompt
    assert '{"' not in initial_prompt


def test_state_change_during_apply_records_a_conflict_solve_attempt() -> None:
    """A stale selected candidate becomes durable conflict evidence, not an error."""

    from restscope.testing.ports import GeneratorConfigConcurrentWrite

    memory = StubMemory()
    application = StubPatchApplication()

    def conflict(**_kwargs):
        raise GeneratorConfigConcurrentWrite("GET /projects/{projectId}")

    application.apply = conflict
    patch_factory = StubPatchFactory([_validated_patch("known-project")])
    client = StubClient(
        [
            _memory_call(),
            _patch_call(),
            _terminal("apply_patch", candidate_ref="P1"),
        ]
    )

    outcome = _start(
        _agent(client, memory, patch_factory, application)
    ).advance()

    assert outcome.status == "conflict"
    assert outcome.solve_attempt_id == "solve-attempt-1"
    assert memory.attempts[0].outcome == "conflict"
    assert memory.attempts[0].conflict_reason is not None
    memory_feedback = client.requests[1].messages[-1].content
    assert memory_feedback.startswith("## PARAMETER path.projectId — UNTRUSTED")
    assert '{"' not in memory_feedback


def test_patch_tool_requires_parameter_history_before_generation() -> None:
    """Scenario: replacement Generator cannot ignore earlier related Failures."""
    memory = StubMemory()
    patch_factory = StubPatchFactory([_validated_patch("unused")])
    client = StubClient(
        [
            _patch_call(),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            memory,
            patch_factory,
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    assert patch_factory.created == []
    assert "Query Parameter memory" in client.requests[1].messages[-1].content
    assert memory.attempts[0].outcome == "no_patch"


def test_multiple_patch_calls_keep_candidates_local_and_apply_selected_one() -> None:
    """Scenario: Solve may reject P1, generate P2, and apply only P2."""
    application = StubPatchApplication()
    client = StubClient(
        [
            _memory_call(),
            _patch_call("patch-1", maximum=100),
            _patch_call("patch-2", maximum=50),
            _terminal("apply_patch", candidate_ref="P2"),
        ]
    )
    patch_factory = StubPatchFactory(
        [
            _validated_patch("first"),
            _validated_patch("second"),
        ]
    )

    outcome = _start(
        _agent(client, StubMemory(), patch_factory, application)
    ).advance()

    assert outcome.status == "applied_patch"
    assert outcome.outputs_used == 8
    assert application.calls[0]["patch"].updates[0].strategy.value == "second"
    assert len(application.calls) == 1


def test_forged_candidate_ref_is_repaired_without_applying_any_patch() -> None:
    """Scenario: final output cannot reference a candidate from another session."""
    application = StubPatchApplication()
    client = StubClient(
        [
            _terminal("apply_patch", candidate_ref="P99"),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(client, StubMemory(), StubPatchFactory([]), application)
    ).advance()

    assert outcome.status == "no_patch"
    assert application.calls == []
    assert "P99" in client.requests[1].messages[-1].content


def test_invalid_multiple_tool_calls_are_not_replayed_without_results() -> None:
    """Rejected calls must not create an invalid provider conversation."""
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="probe-1",
                        name="restscope.http.request",
                        arguments={
                            "method": "GET",
                            "path": "/projects/known",
                        },
                    ),
                    ToolCall(
                        id="memory-1",
                        name="lookup_parameter_history",
                        arguments={"input_handles": ["path.projectId"]},
                    ),
                ],
            ),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    retry_messages = client.requests[1].messages
    assert all(
        call.id not in {"probe-1", "memory-1"}
        for message in retry_messages
        for call in message.tool_calls
    )
    assert "Call exactly one Patch or HTTP tool" in retry_messages[-1].content


def test_multiple_read_only_catalog_queries_make_progress_in_one_output() -> None:
    """Two exact Catalog reads should not be discarded as an invalid output.

    DeepSeek commonly emits several independent Catalog lookups together when
    it needs to compare a request value with the parsed Failure message.  Both
    calls are local and read-only, so rejecting the complete output creates a
    correction loop without protecting any mutable state.
    """
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="catalog-parameter",
                        name="test_case.get_parameter_value",
                        arguments={
                            "case_ids": ["TC1"],
                            "parameter": "path.projectId",
                        },
                    ),
                    ToolCall(
                        id="catalog-failure",
                        name="test_case.get_failure_messages",
                        arguments={
                            "case_ids": ["TC1"],
                        },
                    ),
                    ToolCall(
                        id="parameter-memory",
                        name="lookup_parameter_history",
                        arguments={"input_handles": ["path.projectId"]},
                    ),
                ],
            ),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    retry_messages = client.requests[1].messages
    catalog_results = [
        message
        for message in retry_messages
        if message.role == "tool"
        and message.name is not None
        and message.name.startswith("test_case.")
    ]
    assert {message.tool_call_id for message in catalog_results} == {
        "catalog-parameter",
        "catalog-failure",
    }
    assert any(
        message.role == "tool"
        and message.name == "lookup_parameter_history"
        and message.tool_call_id == "parameter-memory"
        for message in retry_messages
    )


def test_solve_executes_independent_memory_queries_concurrently_in_call_order() -> None:
    """Parallel history reads retain the provider's original result ordering."""
    import threading

    class ConcurrentMemory(StubMemory):
        """Require two Parameter reads to overlap before either can finish."""

        def __init__(self) -> None:
            super().__init__()
            self.barrier = threading.Barrier(2, timeout=1)

        def lookup_parameter_history(self, operation_key, input_node_ids):
            """Wait for the other independent lookup, then return normal data."""
            self.barrier.wait()
            return super().lookup_parameter_history(operation_key, input_node_ids)

    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="path-memory",
                        name="lookup_parameter_history",
                        arguments={"input_handles": ["path.projectId"]},
                    ),
                    ToolCall(
                        id="query-memory",
                        name="lookup_parameter_history",
                        arguments={"input_handles": ["query.region"]},
                    ),
                ],
            ),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            ConcurrentMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    tool_messages = [
        message
        for message in client.requests[1].messages
        if message.role == "tool"
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "path-memory",
        "query-memory",
    ]
    assert "path.projectId" in (tool_messages[0].content or "")
    assert "query.region" in (tool_messages[1].content or "")


def test_invalid_tool_arguments_are_not_replayed_without_a_result() -> None:
    """A rejected single call also stays out of the provider conversation."""
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="invalid-memory",
                        name="lookup_parameter_history",
                        arguments={"input_handles": []},
                    )
                ],
            ),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    retry_messages = client.requests[1].messages
    assert all(
        call.id != "invalid-memory"
        for message in retry_messages
        for call in message.tool_calls
    )
    assert "input_handles must be a non-empty" in retry_messages[-1].content


def test_patch_output_budget_is_part_of_solve_budget() -> None:
    """Scenario: nested Patch exhaustion can consume the final Solve output."""
    failed_patch = ParameterPatchFailure(
        todo_id="T1",
        reason="output_budget_exhausted",
        outputs_used=2,
        errors=["No valid Generator was produced."],
    )
    client = StubClient([_memory_call(), _patch_call()])

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([failed_patch]),
            StubPatchApplication(),
        ),
        max_outputs=4,
    ).advance()

    assert outcome.status == "solve_budget_exhausted"
    assert outcome.outputs_used == 4


def test_solve_tool_contract_exposes_the_shortest_valid_tool_path() -> None:
    """Scenario: a model can discover exact handles and call one tool per output."""
    client = ToolContractAwareClient()
    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([_validated_patch("known-project")]),
            StubPatchApplication(),
        ),
        max_outputs=5,
    ).advance()

    assert outcome.status == "applied_patch"
    assert outcome.outputs_used == 5
    assert len(client.requests) == 3


def test_solve_registers_and_groups_all_three_read_only_openapi_tools() -> None:
    """Solve may inspect independent input and response contracts in one output."""
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="list-inputs",
                        name="openapi.list_inputs",
                        arguments={
                            "operation_key": "GET /projects/{projectId}",
                        },
                    ),
                    ToolCall(
                        id="response-schema",
                        name="openapi.get_response_field_schema",
                        arguments={
                            "operation_key": "GET /projects/{projectId}",
                            "status_code": 404,
                            "field": "body.message",
                        },
                    ),
                ],
            ),
            _terminal("no_patch"),
        ]
    )

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    openapi_names = {
        spec.name
        for spec in client.requests[0].tools
        if spec.name.startswith("openapi.")
    }
    assert openapi_names == {
        "openapi.list_inputs",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
    }
    tool_messages = [
        message
        for message in client.requests[1].messages
        if message.role == "tool"
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "list-inputs",
        "response-schema",
    ]


def test_solve_sends_the_authoritative_terminal_decision_schema() -> None:
    """Provider guidance should expose the exact terminal JSON contract.

    Tools may still be selected in the same request. When the model chooses a
    terminal decision instead, the provider-owned schema prevents repeated
    guesses such as a top-level ``decision`` field or an incomplete
    ``apply_patch`` object.
    """
    client = StubClient([_terminal("no_patch")])

    outcome = _start(
        _agent(
            client,
            StubMemory(),
            StubPatchFactory([]),
            StubPatchApplication(),
        )
    ).advance()

    assert outcome.status == "no_patch"
    request = client.requests[0]
    assert request.response_format == "json_schema"
    assert request.json_schema_name == "FailureSolveDecision"
    assert request.json_schema["properties"]["action"]["enum"] == [
        "apply_patch",
        "no_patch",
        "conflict",
        "continue",
    ]


def test_mutating_failure_solve_receives_the_exact_operation_probe_tool() -> None:
    """DELETE investigations may probe only their exact current operation."""
    client = StubClient([_terminal("no_patch")])
    config = smoke_config()
    config = config.model_copy(
        update={
            "operation_key": "DELETE /projects/{projectId}",
            "snapshot": config.snapshot.model_copy(
                update={
                    "operation_key": "DELETE /projects/{projectId}",
                    "method": "DELETE",
                }
            ),
        }
    )
    request = _request().model_copy(
        update={
            "operation_key": "DELETE /projects/{projectId}",
            "operation": {
                "method": "DELETE",
                "path": "/projects/{projectId}",
            },
        }
    )

    _agent(
        client,
        StubMemory(),
        StubPatchFactory([]),
        StubPatchApplication(),
    ).start(
        request,
        catalog=_catalog(),
        config=config,
        active_constraints=[],
        case_count=2,
        random_seed=731,
    ).advance()

    assert {
        tool.name for tool in client.requests[0].tools
    } == {
        "openapi.list_inputs",
        "openapi.get_input_schema",
        "openapi.get_response_field_schema",
        "lookup_parameter_history",
        "generate_parameter_patch",
        "test_case.get_parameter_value",
        "test_case.find_parameters_by_value",
        "test_case.get_response_field_value",
        "test_case.find_response_fields_by_value",
        "test_case.get_failure_messages",
        "restscope.http.request",
    }


def test_solve_reference_cards_distinguish_response_sources() -> None:
    """Response references expose enough provenance to choose deliberately."""
    client = StubClient([_terminal("no_patch")])
    request = _request().model_copy(
        update={
            "reference_options": [
                {
                    "option_id": "ref-a",
                    "input_node_id": "path/projectId",
                    "kind": "response_value",
                    "value_name": "known_project_id",
                    "compatible_scalar_type": "string",
                    "value_count": 4,
                    "producer_operation_keys": ["GET /projects"],
                    "producer_status_code": "200",
                    "producer_media_type": "application/json",
                    "source_field": "id",
                    "source_selector": "$[].id",
                }
            ]
        }
    )

    _agent(
        client,
        StubMemory(),
        StubPatchFactory([]),
        StubPatchApplication(),
    ).start(
        request,
        catalog=_catalog(),
        config=smoke_config(),
        active_constraints=[],
        case_count=2,
        random_seed=731,
    ).advance()

    prompt = client.requests[0].messages[1].content
    assert "producers=string:\"GET /projects\"" in prompt
    assert "status=string:\"200\"" in prompt
    assert "media=string:\"application/json\"" in prompt
    assert "selector=string:\"$[].id\"" in prompt


def test_solve_uses_an_explicit_complete_system_prompt_override() -> None:
    """An evaluation override replaces only this Solve session's instructions."""
    from restscope.operation_smoke.failure_solver import FailureSolveAgent

    client = StubClient([_terminal("no_patch")])
    agent = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
        memory=StubMemory(),
        patch_coordinator_factory=StubPatchFactory([]),
        patch_application=StubPatchApplication(),
        openapi_capability=_openapi_capability(),
        system_prompt="Candidate Solve instructions.",
    )

    _start(agent).advance()

    assert client.requests[0].messages[0].content == (
        "Candidate Solve instructions."
    )
