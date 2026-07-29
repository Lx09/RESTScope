"""Behavioral contracts for memory-aware Operation Smoke planning."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse, ToolCall
from restscope.operation_smoke.memory import (
    FailureCatalogEntry,
    FailureHistory,
    InvestigationMemory,
    RecordedFailure,
    RecordedPlan,
)


class StubClient:
    """Return prepared model outputs and retain each complete request."""

    def __init__(self, responses: list[LLMResponse | dict]) -> None:
        """Normalize concise decision dictionaries into provider responses."""
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
        """Return the next output in the scripted Agent conversation."""
        self.requests.append(request)
        return self.responses.pop(0)


class StubMemory:
    """Model stable Failure storage without involving SQLAlchemy."""

    def __init__(self) -> None:
        """Seed two histories so tests can exercise multi-reference lookup."""
        self.catalog = [
            FailureCatalogEntry(
                failure_id="db-failure-a",
                summary="Missing project.",
                observation_count=2,
                investigation_count=1,
                applied_patch_count=0,
            ),
            FailureCatalogEntry(
                failure_id="db-failure-b",
                summary="Expired project.",
                observation_count=1,
                investigation_count=1,
                applied_patch_count=1,
            ),
        ]
        self.lookups: list[tuple[str, list[str]]] = []
        self.writes = []

    def list_operation_failures(self, operation_key):
        """Return the compact directory for the requested operation."""
        assert operation_key == "GET /projects/{projectId}"
        return list(self.catalog)

    def lookup_failure_history(self, operation_key, failure_ids):
        """Return complete histories while recording resolved database IDs."""
        self.lookups.append((operation_key, list(failure_ids)))
        return [
            FailureHistory(
                failure_id=failure_id,
                summary=next(
                    item.summary
                    for item in self.catalog
                    if item.failure_id == failure_id
                ),
                investigations=[
                    InvestigationMemory(
                        investigation_id=f"investigation-{failure_id}",
                        round_number=1,
                        outcome="no_patch",
                        trigger_conditions="identifier is unknown",
                        root_cause="the identifier does not exist",
                        solution="no generator change was justified",
                        evidence_source="batch",
                    )
                ],
            )
            for failure_id in failure_ids
        ]

    def record_plan(self, write):
        """Assign stable identities in classification order."""
        self.writes.append(write)
        return RecordedPlan(
            failures=[
                RecordedFailure(
                    failure_id=classification.failure_id
                    or f"db-new-{index}",
                    summary=classification.summary,
                )
                for index, classification in enumerate(
                    write.classifications,
                    start=1,
                )
            ]
        )


def _model() -> LLMModelConfig:
    """Build the configured THINK role used by Planner tests."""
    return LLMModelConfig(
        role="operation_smoke_plan",
        provider="stub",
        model="think-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def _request():
    """Build one Batch with two failed observations and one success."""
    from restscope.operation_smoke.plan import SmokePlanRequest

    return SmokePlanRequest(
        operation_key="GET /projects/{projectId}",
        round_number=2,
        batch_run_id="run-2",
        batch={"run_id": "run-2", "status_code_counts": {"404": 2, "200": 1}},
        coded_cases={
            "C1": {
                "case_id": "case-a",
                "failure": "unexpected status",
                "request": {"path_parameters": {"projectId": "missing-a"}},
                "response": {"status_code": 404},
            },
            "C2": {
                "case_id": "case-b",
                "failure": "unexpected status",
                "request": {"path_parameters": {"projectId": "missing-b"}},
                "response": {"status_code": 404},
            },
            "C3": {
                "case_id": "case-ok",
                "request": {"path_parameters": {"projectId": "known"}},
                "response": {"status_code": 200},
            },
        },
        failed_case_codes=["C1", "C2"],
    )


def _classification(
    *,
    case_codes: list[str] | None = None,
    failure_ref: str | None = "F1",
    disposition: str = "debug",
) -> dict:
    """Build one valid classification with concise defaults."""
    result = {
        "item_id": "T1",
        "failure_ref": failure_ref,
        "summary": "Missing project.",
        "case_codes": case_codes or ["C1", "C2"],
        "disposition": disposition,
        "disposition_reason": None,
    }
    if disposition == "non_debuggable":
        result["disposition_reason"] = "The target requires unavailable authorization."
    return result


def test_plan_queries_multiple_failure_histories_without_exposing_database_ids() -> None:
    """Scenario: model aliases resolve to storage IDs only inside runtime code."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="think-model",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="lookup_failure_history",
                        arguments={"failure_refs": ["F1", "F2"]},
                    )
                ],
            ),
            {
                "action": "process",
                "classifications": [_classification()],
                "reason": "Both observations match the first known Failure.",
            },
        ]
    )
    memory = StubMemory()

    plan = SmokePlanAgent(
        client=client,
        model=_model(),
        memory=memory,
    ).plan(_request())

    assert plan.status == "planned"
    assert plan.outputs_used == 2
    assert memory.lookups == [
        (
            "GET /projects/{projectId}",
            ["db-failure-a", "db-failure-b"],
        )
    ]
    tool_message = client.requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert '"failure_ref":"F1"' in tool_message.content
    assert "db-failure-a" not in tool_message.content


def test_valid_plan_is_written_once_and_expanded_to_stable_failure_todo() -> None:
    """Scenario: memory changes only after complete semantic validation."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    memory = StubMemory()
    plan = SmokePlanAgent(
        client=StubClient(
            [
                {
                    "action": "process",
                    "classifications": [_classification()],
                    "reason": "One known Failure covers both observations.",
                }
            ]
        ),
        model=_model(),
        memory=memory,
    ).plan(_request())

    assert len(memory.writes) == 1
    write = memory.writes[0]
    assert write.classifications[0].failure_id == "db-failure-a"
    assert len(write.classifications[0].observations) == 2
    assert plan.todos[0].failure_id == "db-failure-a"
    assert [case["case_id"] for case in plan.todos[0].cases] == [
        "case-a",
        "case-b",
    ]


def test_plan_rejects_duplicate_failure_items_and_incomplete_coverage() -> None:
    """Scenario: one known Failure cannot fork and every failed case is covered."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    memory = StubMemory()
    client = StubClient(
        [
            {
                "action": "process",
                "classifications": [
                    _classification(case_codes=["C1"]),
                    {
                        **_classification(case_codes=["C1"]),
                        "item_id": "T2",
                    },
                ],
                "reason": "Invalid duplicate.",
            }
        ]
    )

    plan = SmokePlanAgent(
        client=client,
        model=_model(),
        memory=memory,
    ).plan(_request(), max_outputs=1)

    assert plan.status == "plan_budget_exhausted"
    assert "only once" in plan.reason
    assert "C2" in plan.reason
    assert memory.writes == []


def test_non_debuggable_classification_records_reason_and_returns_no_debug() -> None:
    """Scenario: Planner may stop only while retaining classified evidence."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    memory = StubMemory()
    plan = SmokePlanAgent(
        client=StubClient(
            [
                {
                    "action": "no_debug",
                    "classifications": [
                        _classification(disposition="non_debuggable")
                    ],
                    "reason": "No current Failure can be debugged safely.",
                }
            ]
        ),
        model=_model(),
        memory=memory,
    ).plan(_request())

    assert plan.status == "no_debug"
    assert plan.todos == []
    assert plan.non_debuggable[0].reason.startswith("The target")
    assert (
        memory.writes[0].classifications[0].disposition
        == "non_debuggable"
    )


def test_forged_memory_ref_consumes_budget_without_reading_or_writing() -> None:
    """Scenario: a model cannot query a Failure outside its supplied catalog."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    memory = StubMemory()
    plan = SmokePlanAgent(
        client=StubClient(
            [
                LLMResponse(
                    provider="stub",
                    model="think-model",
                    tool_calls=[
                        ToolCall(
                            id="call-forged",
                            name="lookup_failure_history",
                            arguments={"failure_refs": ["F99"]},
                        )
                    ],
                )
            ]
        ),
        model=_model(),
        memory=memory,
    ).plan(_request(), max_outputs=1)

    assert plan.status == "plan_budget_exhausted"
    assert "F99" in plan.reason
    assert memory.lookups == []
    assert memory.writes == []
