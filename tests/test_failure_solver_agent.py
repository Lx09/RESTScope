"""Behavioral contracts for one continuous failure-solving conversation."""

from __future__ import annotations

from restscope.llm import (
    LLMModelConfig,
    LLMResponse,
    ToolCall,
    ToolResult,
    ToolSpec,
)


class StubClient:
    """Return prepared model responses and expose every request."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next response."""
        self.requests.append(request)
        return self.responses.pop(0)


class StubProbe:
    """Validate and execute current-operation probes without network access."""

    def __init__(self, invalid_ids: set[str] | None = None) -> None:
        self.invalid_ids = set(invalid_ids or ())
        self.executed: list[str] = []

    def tool_spec(self, config):
        """Describe the only tool available to a normal Solve output."""
        return ToolSpec(
            name="restscope.http.request",
            description="Probe the current operation.",
            kind="local_function",
            input_schema={"type": "object"},
        )

    def validate(self, *, config, tool_call):
        """Reject selected calls before any call in that output executes."""
        if tool_call.id in self.invalid_ids:
            return f"{tool_call.id} is outside the current operation"
        return None

    def execute(self, *, config, tool_call):
        """Return one bounded observation."""
        self.executed.append(tool_call.id)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={
                "status_code": 404,
                "json": {"error": "project missing"},
            },
        )


def _response(payload=None, *, calls: list[ToolCall] | None = None):
    """Build one provider-neutral model response."""
    return LLMResponse(
        provider="stub",
        model="think-model",
        parsed_json=payload,
        tool_calls=list(calls or []),
    )


def _tool_call(call_id: str) -> ToolCall:
    """Build one scoped HTTP request chosen by the model."""
    return ToolCall(
        id=call_id,
        name="restscope.http.request",
        arguments={"method": "GET", "path": "/projects/missing"},
    )


def _model() -> LLMModelConfig:
    """Build the THINK model selected for failure solving."""
    return LLMModelConfig(
        role="operation_smoke_failure_solve",
        provider="stub",
        model="think-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _request():
    """Build a todo whose evidence has already been expanded by Plan."""
    from restscope.agent.failure_solver import FailureSolveRequest

    return FailureSolveRequest(
        operation_key="GET /projects/{projectId}",
        todo={
            "todo_id": "T1",
            "failure": "Project lookup returns not found.",
            "cases": [
                {
                    "case_id": "case-a",
                    "request": {"path": "/projects/missing"},
                    "response": {
                        "status_code": 404,
                        "json": {"error": "project missing"},
                    },
                }
            ],
        },
        operation={
            "method": "GET",
            "path": "/projects/{projectId}",
            "parameters": [{"name": "projectId", "in": "path"}],
        },
        generator_config={
            "revision": 1,
            "inputs": [{"path": "path.projectId", "type": "string"}],
        },
        current_batch={"run_id": "run-1", "status_code_counts": {"404": 1}},
        reference_options=[],
        history=[],
    )


def _config():
    """Provide the minimal frozen operation config required by a probe."""
    from tests._operation_smoke_plan_solve_fixtures import smoke_config

    return smoke_config()


def test_solve_keeps_tool_observations_in_one_continuous_session() -> None:
    """Scenario: HTTP evidence leads to a complete PatchRequirement."""
    from restscope.agent.failure_solver import FailureSolveAgent

    client = StubClient(
        [
            _response(calls=[_tool_call("call-1")]),
            _response(
                {
                    "action": "patch_ready",
                    "patch_requirement": {
                        "root_cause": "The identifier does not exist.",
                        "affected_inputs": ["path.projectId"],
                        "desired_behavior": "Use an observed project identifier.",
                        "acceptance_criteria": (
                            "The project-missing response disappears."
                        ),
                    },
                }
            ),
        ]
    )
    probe = StubProbe()

    session = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=probe,
    ).start(_request(), config=_config())
    outcome = session.advance()

    assert outcome.status == "patch_ready"
    assert outcome.outputs_used == 2
    assert len(outcome.output_history) == 2
    assert outcome.patch_requirement.affected_inputs == ["path.projectId"]
    assert probe.executed == ["call-1"]
    second_prompt = client.requests[1].messages
    assert any(message.role == "tool" for message in second_prompt)
    assert "C1" not in str(second_prompt)
    assert "project missing" in str(second_prompt)


def test_solve_checkpoint_disables_tools_and_requires_continuation() -> None:
    """Scenario: output 10 is a model-owned stop/continue checkpoint."""
    from restscope.agent.failure_solver import FailureSolveAgent

    responses = [
        _response(calls=[_tool_call(f"call-{index}")])
        for index in range(1, 10)
    ]
    responses.extend(
        [
            _response(
                {
                    "action": "continue",
                    "reason": "A different identifier source remains untested.",
                    "next_step": "Probe the identifier returned by the list endpoint.",
                }
            ),
            _response(
                {
                    "action": "finish",
                    "finish_status": "insufficient_evidence",
                    "reason": "The producer operation is unavailable.",
                }
            ),
        ]
    )
    client = StubClient(responses)

    outcome = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
    ).start(
        _request(),
        config=_config(),
        max_outputs=50,
        continuation_interval=10,
    ).advance()

    assert outcome.status == "insufficient_evidence"
    assert outcome.outputs_used == 11
    assert client.requests[9].tools == []
    assert "continue or stop" in client.requests[9].messages[-1].content
    assert client.requests[10].tools


def test_solve_counts_invalid_outputs_until_budget_exhaustion() -> None:
    """Scenario: malformed replies cannot escape the per-todo output budget."""
    from restscope.agent.failure_solver import FailureSolveAgent

    client = StubClient([_response({"unexpected": True}) for _ in range(3)])

    outcome = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
    ).start(
        _request(),
        config=_config(),
        max_outputs=3,
        continuation_interval=10,
    ).advance()

    assert outcome.status == "solve_budget_exhausted"
    assert outcome.outputs_used == 3
    assert len(client.requests) == 3


def test_invalid_tool_batch_executes_no_http_request() -> None:
    """Scenario: one out-of-scope call rejects its entire model output."""
    from restscope.agent.failure_solver import FailureSolveAgent

    probe = StubProbe(invalid_ids={"bad"})
    client = StubClient(
        [
            _response(calls=[_tool_call("good"), _tool_call("bad")]),
            _response(
                {
                    "action": "finish",
                    "finish_status": "dependency_related",
                    "reason": "The failure requires another operation first.",
                }
            ),
        ]
    )

    outcome = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=probe,
    ).start(_request(), config=_config()).advance()

    assert outcome.status == "dependency_related"
    assert probe.executed == []
    assert "bad is outside" in client.requests[1].messages[-1].content


def test_effect_feedback_resumes_the_same_solve_conversation() -> None:
    """Scenario: rejected effect evidence informs a later PatchRequirement."""
    from restscope.agent.failure_solver import FailureSolveAgent

    client = StubClient(
        [
            _response(
                {
                    "action": "patch_ready",
                    "patch_requirement": {
                        "root_cause": "The ID is unknown.",
                        "affected_inputs": ["path.projectId"],
                        "desired_behavior": "Use a candidate identifier.",
                        "acceptance_criteria": "The 404 disappears.",
                    },
                }
            ),
            _response(
                {
                    "action": "patch_ready",
                    "patch_requirement": {
                        "root_cause": "The first pool contained stale IDs.",
                        "affected_inputs": ["path.projectId"],
                        "desired_behavior": "Use identifiers from the live list.",
                        "acceptance_criteria": "The 404 disappears without regression.",
                    },
                }
            ),
        ]
    )
    session = FailureSolveAgent(
        client=client,
        model=_model(),
        http_probe=StubProbe(),
    ).start(_request(), config=_config())

    first = session.advance()
    second = session.advance(
        feedback={
            "effect": "unresolved",
            "candidate_response": {"status_code": 404, "json": {"error": "stale"}},
        }
    )

    assert first.outputs_used == 1
    assert second.outputs_used == 2
    assert "stale" in client.requests[1].messages[-1].content
