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

from tests._operation_smoke_plan_solve_fixtures import smoke_config


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
        )

    def validate(self, *, config, tool_call):
        """Accept only the expected HTTP tool name in focused tests."""
        if tool_call.name != "restscope.http.request":
            return f"{tool_call.name} is not the HTTP probe"
        return None

    def execute(self, *, config, tool_call):
        """Return one bounded status observation."""
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
        self.investigations = []

    def lookup_failure_history(self, operation_key, failure_ids):
        """Return the current Failure's complete but empty Investigation history."""
        self.failure_lookups.append((operation_key, list(failure_ids)))
        return [
            FailureHistory(
                failure_id=failure_ids[0],
                summary="Project identifier is rejected.",
            )
        ]

    def lookup_parameter_history(self, operation_key, input_node_ids):
        """Return an entry for every resolved operation-local input node."""
        self.parameter_lookups.append((operation_key, list(input_node_ids)))
        return [
            ParameterHistory(input_node_id=input_node_id)
            for input_node_id in input_node_ids
        ]

    def record_investigation(self, write):
        """Record terminal no-Patch/conflict decisions."""
        self.investigations.append(write)
        return f"investigation-{len(self.investigations)}"


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
        """Return the next revision and retain the exact persistence request."""
        self.calls.append(kwargs)
        return AppliedSmokePatch(
            config=smoke_config().model_copy(update={"revision": 2}),
            investigation_id="investigation-applied",
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
    """Build one stable Failure with expanded current Batch evidence."""
    from restscope.operation_smoke.failure_solver import FailureSolveRequest

    return FailureSolveRequest(
        operation_key="GET /projects/{projectId}",
        round_number=2,
        todo={
            "todo_id": "T1",
            "failure_id": "db-failure-1",
            "failure": "Project identifier is rejected.",
            "cases": [
                {
                    "case_id": "case-a",
                    "request": {"path": "/projects/missing"},
                    "response": {"status_code": 404},
                }
            ],
        },
        operation={"method": "GET", "path": "/projects/{projectId}"},
        generator_config={"revision": 1},
        current_batch={"run_id": "run-2", "status_code_counts": {"404": 1}},
    )


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
        patch_agent_factory=patch_factory,
        patch_application=application,
    )


def _start(agent, **kwargs):
    """Start with the shared seed and empty active Constraint set."""
    return agent.start(
        _request(),
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
    assert outcome.active_config_revision == 2
    assert memory.failure_lookups == [
        ("GET /projects/{projectId}", ["db-failure-1"])
    ]
    assert memory.parameter_lookups == [
        ("GET /projects/{projectId}", ["path/projectId"])
    ]
    assert len(application.calls) == 1
    assert application.calls[0]["patch"].updates[0].strategy.value == "known-project"
    assert patch_factory.created[0].calls[0]["random_seed"] == 731
    assert patch_factory.created[0].calls[0]["task"].prior_attempts
    initial_prompt = client.requests[0].messages[1].content
    assert "CURRENT FAILURE CASES" in initial_prompt
    assert "path.projectId" in initial_prompt
    assert "run-2" not in initial_prompt
    assert "current_batch" not in initial_prompt
    assert '{"' not in initial_prompt
    memory_feedback = client.requests[1].messages[-1].content
    assert memory_feedback.startswith("PARAMETER path.projectId")
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
    assert memory.investigations[0].outcome == "no_patch"


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


def test_solve_uses_an_explicit_complete_system_prompt_override() -> None:
    """Scenario: evaluation replaces only this Investigation's instructions."""
    from restscope.operation_smoke.failure_solver import FailureSolveAgent

    client = StubClient([_terminal("no_patch")])
    agent = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
        memory=StubMemory(),
        patch_agent_factory=StubPatchFactory([]),
        patch_application=StubPatchApplication(),
        system_prompt="Candidate Solve instructions.",
    )

    _start(agent).advance()

    assert client.requests[0].messages[0].content == (
        "Candidate Solve instructions."
    )
