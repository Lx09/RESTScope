"""Behavioral contracts for memory-aware Operation Smoke planning."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse
from restscope.operation_smoke.memory import (
    FailureCatalogEntry,
    FailureCandidate,
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
        self.retrievals = []
        self.writes = []

    def find_failure_candidates(self, operation_key, observations):
        """Return ranked candidates and retain the typed retrieval observations."""
        assert operation_key == "GET /projects/{projectId}"
        self.retrievals.append((operation_key, list(observations)))
        return [
            FailureCandidate(
                failure_id=item.failure_id,
                summary=item.summary,
                matched_case_codes=["C1", "C2"],
                match_reasons=["same-status-kind-and-input"],
                observation_count=item.observation_count,
                investigation_count=item.investigation_count,
                applied_patch_count=item.applied_patch_count,
                last_seen_round=1,
                recent_investigations=[
                    InvestigationMemory(
                        investigation_id=f"investigation-{item.failure_id}",
                        round_number=1,
                        outcome="no_patch",
                        trigger_conditions="identifier is unknown",
                        root_cause="the identifier does not exist",
                        solution="no generator change was justified",
                        evidence_source="batch",
                    )
                ],
            )
            for item in self.catalog
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
                "generated_test_case": {
                    "path_parameters": {"projectId": "missing-a"},
                    "query_parameters": {"min_access_level": 25},
                    "header_parameters": {"X-Scenario": "generated"},
                    "cookie_parameters": {},
                    "body": None,
                    "body_present": False,
                },
                "request": {
                    "path": "/projects/missing-a",
                    "query_items": [["min_access_level", "25"]],
                    "headers": {
                        "Authorization": "[redacted]",
                        "X-Scenario": "generated",
                    },
                },
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


def test_plan_receives_ranked_candidates_without_a_memory_tool_or_database_ids() -> None:
    """Scenario: runtime retrieves candidates before the one-output Plan call."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    client = StubClient(
        [
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
    assert plan.outputs_used == 1
    assert len(memory.retrievals) == 1
    assert [item.case_code for item in memory.retrievals[0][1]] == ["C1", "C2"]
    request = client.requests[0]
    assert request.tools == []
    assert request.tool_choice == "none"
    prompt = request.messages[1].content
    assert "F1" in prompt
    assert "C3" not in prompt
    assert "db-failure-a" not in prompt
    assert "path_parameters.projectId=string:\"missing-a\"" in prompt
    assert "query.min_access_level=int:25" in prompt
    assert "headers.X-Scenario=string:\"generated\"" in prompt
    assert "Authorization" not in prompt
    assert '{"' not in prompt


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


def test_forged_candidate_ref_consumes_budget_without_writing() -> None:
    """Scenario: a model cannot reuse a Failure outside ranked candidates."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    memory = StubMemory()
    plan = SmokePlanAgent(
        client=StubClient(
            [
                {
                    "action": "process",
                    "classifications": [
                        _classification(failure_ref="F99")
                    ],
                    "reason": "Forged candidate.",
                }
            ]
        ),
        model=_model(),
        memory=memory,
    ).plan(_request(), max_outputs=1)

    assert plan.status == "plan_budget_exhausted"
    assert "F99" in plan.reason
    assert memory.writes == []


def test_plan_uses_an_explicit_complete_system_prompt_override() -> None:
    """Scenario: evaluation can compare a candidate without changing production."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    client = StubClient(
        [
            {
                "action": "process",
                "classifications": [_classification()],
                "reason": "One known Failure covers both observations.",
            }
        ]
    )

    SmokePlanAgent(
        client=client,
        model=_model(),
        memory=StubMemory(),
        system_prompt="Candidate Planner instructions.",
    ).plan(_request())

    assert client.requests[0].messages[0].content == (
        "Candidate Planner instructions."
    )


def test_plan_prompt_keeps_actionable_failures_debuggable() -> None:
    """Scenario: generated-value and transport evidence must reach Solve."""
    from restscope.operation_smoke.plan.agent import _system_prompt

    prompt = _system_prompt()

    assert (
        "Rejected generated values, target validation failures, response "
        "mismatches, missing resources, and transport errors are debuggable."
        in prompt
    )
    assert (
        "non_debuggable is only for evidence that no safe current "
        "investigation can clarify."
        in prompt
    )
    assert (
        "Use action=process when any classification has disposition=debug; "
        "otherwise use action=no_debug."
        in prompt
    )
    assert (
        "Use disposition=debug without disposition_reason for debuggable "
        "evidence. Use disposition=non_debuggable with a concrete "
        "disposition_reason only for non-debuggable evidence."
        in prompt
    )
    assert (
        "reason must be a non-empty string summarizing the classification; "
        "never return null."
        in prompt
    )


def test_plan_caps_a_large_retrieval_result_at_24_candidate_cards() -> None:
    """Scenario: hundreds of historical Failures cannot flood Planner context."""
    from restscope.operation_smoke.plan import SmokePlanAgent

    class LargeCandidateMemory(StubMemory):
        """Return more candidates than the public Planner prompt allowance."""

        def find_failure_candidates(self, operation_key, observations):
            self.retrievals.append((operation_key, list(observations)))
            return [
                FailureCandidate(
                    failure_id=f"failure-{index}",
                    summary=f"Historical Failure {index}.",
                    matched_case_codes=["C1"],
                    match_reasons=["shared-terms:project"],
                    observation_count=1,
                    investigation_count=0,
                    applied_patch_count=0,
                    last_seen_round=1,
                )
                for index in range(1, 501)
            ]

    client = StubClient(
        [
            {
                "action": "process",
                "classifications": [_classification(failure_ref="F1")],
                "reason": "Reuse the first ranked candidate.",
            }
        ]
    )
    SmokePlanAgent(
        client=client,
        model=_model(),
        memory=LargeCandidateMemory(),
    ).plan(_request())

    prompt = client.requests[0].messages[1].content
    assert "F24 |" in prompt
    assert "F25 |" not in prompt
    assert "maximum=int:24" in prompt
