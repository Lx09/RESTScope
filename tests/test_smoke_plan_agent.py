"""Behavioral contracts for the LLM-led Operation Smoke planning Agent."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse


class StubClient:
    """Return prepared model outputs and retain every request for assertions."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next structured response."""
        self.requests.append(request)
        return LLMResponse(
            provider="stub",
            model="think-model",
            parsed_json=self.responses.pop(0),
        )


def _model() -> LLMModelConfig:
    """Build the configured THINK role used by planning tests."""
    return LLMModelConfig(
        role="operation_smoke_plan",
        provider="stub",
        model="think-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _request():
    """Build one complete coded batch whose evidence must later be expanded."""
    from restscope.agent.smoke_plan import SmokePlanRequest

    return SmokePlanRequest(
        operation_key="GET /projects/{projectId}",
        batch={
            "run_id": "run-1",
            "status_code_counts": {"404": 2, "200": 1},
        },
        coded_cases={
            "C1": {
                "case_id": "case-a",
                "request": {"path": "/projects/missing-a"},
                "response": {"status_code": 404, "json": {"error": "missing"}},
            },
            "C2": {
                "case_id": "case-b",
                "request": {"path": "/projects/missing-b"},
                "response": {"status_code": 404, "json": {"error": "missing"}},
            },
            "C3": {
                "case_id": "case-ok",
                "request": {"path": "/projects/known"},
                "response": {"status_code": 200},
            },
        },
        failed_case_codes=["C1", "C2"],
        history=[
            {
                "round": 0,
                "outcome": "A previous format-only attempt was rejected.",
            }
        ],
    )


def test_plan_expands_case_codes_before_returning_todos() -> None:
    """Scenario: temporary Plan codes never become downstream evidence."""
    from restscope.agent.smoke_plan import SmokePlanAgent

    client = StubClient(
        [
            {
                "action": "process",
                "todos": [
                    {
                        "todo_id": "T1",
                        "failure": "Project lookup returns not found.",
                        "case_codes": ["C1", "C2"],
                    }
                ],
                "reason": "One unique failure covers both failed cases.",
            }
        ]
    )

    plan = SmokePlanAgent(client=client, model=_model()).plan(_request())

    assert plan.status == "planned"
    assert plan.outputs_used == 1
    assert plan.todos[0].failure == "Project lookup returns not found."
    assert [case["case_id"] for case in plan.todos[0].cases] == [
        "case-a",
        "case-b",
    ]
    downstream = plan.todos[0].model_dump(mode="json")
    assert "case_codes" not in downstream
    assert "C1" not in str(downstream)
    assert "C2" not in str(downstream)


def test_plan_repairs_unknown_references_and_counts_every_output() -> None:
    """Scenario: an invalid reference consumes budget before correction."""
    from restscope.agent.smoke_plan import SmokePlanAgent

    client = StubClient(
        [
            {
                "action": "process",
                "todos": [
                    {
                        "todo_id": "T1",
                        "failure": "Missing project.",
                        "case_codes": ["C9"],
                    }
                ],
                "reason": "First attempt.",
            },
            {
                "action": "process",
                "todos": [
                    {
                        "todo_id": "T1",
                        "failure": "Missing project.",
                        "case_codes": ["C1", "C2"],
                    }
                ],
                "reason": "Corrected references.",
            },
        ]
    )

    plan = SmokePlanAgent(client=client, model=_model()).plan(
        _request(),
        max_outputs=2,
    )

    assert plan.status == "planned"
    assert plan.outputs_used == 2
    assert len(client.requests) == 2
    assert "C9 was not supplied" in client.requests[1].messages[-1].content


def test_plan_requires_every_failed_case_to_be_managed() -> None:
    """Scenario: Plan cannot silently omit a failed case from its todo list."""
    from restscope.agent.smoke_plan import SmokePlanAgent

    client = StubClient(
        [
            {
                "action": "process",
                "todos": [
                    {
                        "todo_id": "T1",
                        "failure": "Only the first case.",
                        "case_codes": ["C1"],
                    }
                ],
                "reason": "Incomplete.",
            }
        ]
    )

    plan = SmokePlanAgent(client=client, model=_model()).plan(
        _request(),
        max_outputs=1,
    )

    assert plan.status == "plan_budget_exhausted"
    assert plan.outputs_used == 1
    assert "C2" in plan.reason


def test_plan_can_end_when_history_contains_no_new_failure_work() -> None:
    """Scenario: Plan owns the semantic decision that recorded work is exhausted."""
    from restscope.agent.smoke_plan import SmokePlanAgent

    client = StubClient(
        [
            {
                "action": "no_new_failure_work",
                "todos": [],
                "reason": "Every current failure has a terminal history record.",
            }
        ]
    )

    plan = SmokePlanAgent(client=client, model=_model()).plan(_request())

    assert plan.status == "no_new_failure_work"
    assert plan.todos == []
    assert plan.outputs_used == 1
