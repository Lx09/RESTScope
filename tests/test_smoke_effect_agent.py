"""Behavioral contracts for independent candidate effect validation."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse


class StubClient:
    """Return prepared Effect outputs and retain requests."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next effect decision."""
        self.requests.append(request)
        return LLMResponse(
            provider="stub",
            model="think-model",
            parsed_json=self.responses.pop(0),
        )


def _model() -> LLMModelConfig:
    """Build the THINK model used by effect validation."""
    return LLMModelConfig(
        role="operation_smoke_effect_validation",
        provider="stub",
        model="think-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _request():
    """Build full before/candidate evidence with no temporary aliases."""
    from restscope.agent.smoke_effect import SmokeEffectRequest

    return SmokeEffectRequest(
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
        patch_requirement={
            "root_cause": "The generated ID does not exist.",
            "affected_inputs": ["path.projectId"],
            "desired_behavior": "Use an existing project ID.",
            "acceptance_criteria": "The project-missing response disappears.",
        },
        patch={
            "updates": [
                {
                    "input_node_id": "path/projectId",
                    "strategy": {"type": "constant", "value": "known-project"},
                }
            ]
        },
        before_batch={
            "run_id": "before",
            "cases": [
                {
                    "case_index": 0,
                    "request": {"path": "/projects/missing"},
                    "response": {
                        "status_code": 404,
                        "json": {"error": "project missing"},
                    },
                }
            ],
        },
        candidate_batch={
            "run_id": "candidate",
            "cases": [
                {
                    "case_index": 0,
                    "request": {"path": "/projects/known-project"},
                    "response": {"status_code": 200, "json": {"id": "known-project"}},
                }
            ],
        },
        history=[],
    )


def test_effect_accepts_only_resolved_without_regression() -> None:
    """Scenario: one valid semantic decision accepts an atomic candidate."""
    from restscope.agent.smoke_effect import SmokeEffectAgent

    client = StubClient(
        [
            {
                "outcome": "resolved_without_regression",
                "reason": "The target 404 disappeared and the aligned case is 2xx.",
            }
        ]
    )

    outcome = SmokeEffectAgent(client=client, model=_model()).validate(
        _request()
    )

    assert outcome.outcome == "resolved_without_regression"
    assert outcome.outputs_used == 1
    prompt = client.requests[0].messages[-1].content
    assert "project missing" in prompt
    assert "known-project" in prompt
    assert '"C1"' not in prompt


def test_effect_repairs_once_then_reports_regression() -> None:
    """Scenario: the second and final output can repair an invalid protocol."""
    from restscope.agent.smoke_effect import SmokeEffectAgent

    client = StubClient(
        [
            {"status": "fixed"},
            {
                "outcome": "regression",
                "reason": "A previously successful aligned case now returns 500.",
            },
        ]
    )

    outcome = SmokeEffectAgent(client=client, model=_model()).validate(
        _request(),
        max_outputs=2,
    )

    assert outcome.outcome == "regression"
    assert outcome.outputs_used == 2
    assert len(outcome.output_history) == 2
    assert "could not be used" in client.requests[1].messages[-1].content


def test_effect_two_invalid_outputs_fail_closed_as_unknown() -> None:
    """Scenario: malformed validation never accepts a candidate."""
    from restscope.agent.smoke_effect import SmokeEffectAgent

    client = StubClient([{"invalid": 1}, {"invalid": 2}])

    outcome = SmokeEffectAgent(client=client, model=_model()).validate(
        _request(),
        max_outputs=2,
    )

    assert outcome.outcome == "unknown"
    assert outcome.outputs_used == 2
    assert "output budget" in outcome.reason
