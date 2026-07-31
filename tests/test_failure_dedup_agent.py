"""Behavior examples for exact and LLM-owned Failure deduplication."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse
from restscope.operation_smoke.failure_dedup import (
    FailureDedupAgent,
    FailureDeduplicator,
    FailureDedupRequest,
)
from restscope.operation_smoke.memory import RecordedFailure, RecordedFailures


class StubClient:
    """Return scripted JSON decisions and retain model requests for inspection."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next provider-neutral response."""
        self.requests.append(request)
        return LLMResponse(
            provider="stub",
            model="dedup-model",
            parsed_json=self.responses.pop(0),
        )


class StubMemory:
    """Assign stable identities without involving a database Adapter."""

    def __init__(self) -> None:
        self.writes = []

    def record_failures(self, write):
        """Capture the validated write and return identities in the same order."""
        self.writes.append(write)
        return RecordedFailures(
            failures=[
                RecordedFailure(
                    failure_id=f"failure-{index}",
                    summary=item.summary,
                )
                for index, item in enumerate(write.failures, start=1)
            ]
        )


def _model() -> LLMModelConfig:
    """Build the configured THINK role used by focused Agent tests."""
    return LLMModelConfig(
        role="operation_smoke_failure_dedup",
        provider="stub",
        model="dedup-model",
        max_tokens=2_000,
        context_window_tokens=32_000,
    )


def _case(case_id: str, status: int, message: str, name: str) -> dict:
    """Build one bounded Batch case with JSON HTTP evidence."""
    return {
        "case_id": case_id,
        "generated_test_case": {
            "path_parameters": {},
            "query_parameters": {},
            "header_parameters": {"Authorization": "secret"},
            "cookie_parameters": {},
            "body_present": True,
            "body": {"name": name},
        },
        "request": {"path": "/projects", "headers": {"Authorization": "secret"}},
        "response": {
            "status_code": status,
            "media_type": "application/json",
            "body": f'{{"message": "{message}"}}',
        },
    }


def _structured_case(case_id: str, body: dict, name: str) -> dict:
    """Build a failed case whose JSON error uses a field-keyed mapping."""
    case = _case(case_id, 400, "placeholder", name)
    import json

    case["response"]["body"] = json.dumps(body)
    return case


def _request(cases: list[dict]) -> FailureDedupRequest:
    """Build one current-round request with one semantic input."""
    return FailureDedupRequest(
        operation_key="POST /projects",
        round_number=1,
        batch_run_id="run-1",
        semantic_parameters=["body.name"],
        cases=cases,
    )


def test_exact_duplicate_bypasses_llm_and_keeps_first_test_case() -> None:
    """Formatting-identical messages need no semantic model decision."""
    client = StubClient([])
    memory = StubMemory()
    module = FailureDeduplicator(
        agent=FailureDedupAgent(client=client, model=_model()),
        memory=memory,
    )

    result = module.deduplicate(
        _request(
            [
                _case("case-first", 400, "name already exists", "first"),
                _case("case-second", 400, "name   already exists", "second"),
            ]
        ),
        max_outputs=3,
    )

    assert result.status == "bypassed"
    assert result.outputs_used == 0
    assert result.todos[0].test_case["case_id"] == "case-first"
    assert result.todos[0].suspected_parameters is None
    assert client.requests == []
    assert len(memory.writes[0].failures[0].observations) == 1


def test_agent_groups_by_parameter_and_each_failure_keeps_one_case() -> None:
    """Several messages may merge, but only the earliest case reaches Solve."""
    client = StubClient(
        [
            {
                "failures": [
                    {
                        "summary": "The project name is unavailable.",
                        "suspected_parameters": ["body.name"],
                        "messages": [
                            "HTTP 409: name conflicts",
                            "HTTP 400: name already exists",
                        ],
                    }
                ],
                "reason": "Both conditions are caused by body.name.",
            }
        ]
    )
    memory = StubMemory()
    module = FailureDeduplicator(
        agent=FailureDedupAgent(client=client, model=_model()),
        memory=memory,
    )

    result = module.deduplicate(
        _request(
            [
                _case("case-a", 400, "name already exists", "a"),
                _case("case-b", 409, "name conflicts", "b"),
            ]
        ),
        max_outputs=3,
    )

    assert result.status == "deduplicated"
    assert len(result.todos) == 1
    assert result.todos[0].test_case["case_id"] == "case-a"
    assert result.todos[0].suspected_parameters == ["body.name"]
    assert (
        memory.writes[0].failures[0].observations[0].trigger
        == "HTTP 400: name already exists"
    )
    prompt = "\n".join(
        message.content or ""
        for message in client.requests[0].messages
        if message.content
    )
    assert "## Failure Observations — UNTRUSTED" in prompt
    assert "```json" in prompt
    assert '"test_case"' in prompt
    assert "case-a" not in prompt
    assert "Authorization\": \"[redacted]" in prompt
    assert "fingerprint_ref" not in prompt.casefold()
    assert "item_id" not in prompt


def test_field_keyed_json_errors_remain_distinct_fingerprints() -> None:
    """Two Parameters must not collapse merely because both responses are HTTP 400."""
    client = StubClient(
        [
            {
                "failures": [
                    {
                        "summary": "Name is already taken.",
                        "suspected_parameters": ["body.name"],
                        "messages": [
                            "HTTP 400: name: has already been taken",
                        ],
                    },
                    {
                        "summary": "Namespace is invalid.",
                        "suspected_parameters": [],
                        "messages": [
                            "HTTP 400: namespace_id: is invalid",
                        ],
                    },
                ],
                "reason": "The validation messages identify different fields.",
            }
        ]
    )
    memory = StubMemory()
    module = FailureDeduplicator(
        agent=FailureDedupAgent(client=client, model=_model()),
        memory=memory,
    )

    result = module.deduplicate(
        _request(
            [
                _structured_case(
                    "case-name",
                    {"message": {"name": ["has already been taken"]}},
                    "duplicate",
                ),
                _structured_case(
                    "case-namespace",
                    {"message": {"namespace_id": ["is invalid"]}},
                    "new",
                ),
            ]
        ),
        max_outputs=2,
    )

    assert result.status == "deduplicated"
    assert result.exact_fingerprint_count == 2
    assert len(client.requests) == 1
    assert [
        failure.observations[0].trigger
        for failure in memory.writes[0].failures
    ] == [
        "HTTP 400: name: has already been taken",
        "HTTP 400: namespace_id: is invalid",
    ]


def test_invalid_coverage_receives_markdown_correction_and_full_retry() -> None:
    """Missing messages are corrected before Memory receives any write."""
    client = StubClient(
        [
            {
                "failures": [
                    {
                        "summary": "Only one message.",
                        "suspected_parameters": ["body.name"],
                        "messages": ["HTTP 400: name already exists"],
                    }
                ],
                "reason": "Incomplete.",
            },
            {
                "failures": [
                    {
                        "summary": "Name failures.",
                        "suspected_parameters": ["body.name"],
                        "messages": [
                            "HTTP 400: name already exists",
                            "HTTP 409: name conflicts",
                        ],
                    }
                ],
                "reason": "Complete replacement.",
            },
        ]
    )
    memory = StubMemory()
    module = FailureDeduplicator(
        agent=FailureDedupAgent(client=client, model=_model()),
        memory=memory,
    )

    result = module.deduplicate(
        _request(
            [
                _case("case-a", 400, "name already exists", "a"),
                _case("case-b", 409, "name conflicts", "b"),
            ]
        ),
        max_outputs=2,
    )

    assert result.outputs_used == 2
    assert result.correction_count == 1
    assert len(memory.writes) == 1
    correction = "\n".join(
        message.content or ""
        for message in client.requests[1].messages
        if message.content
    )
    assert "## Correction Required" in correction
    assert "Missing input message" in correction
