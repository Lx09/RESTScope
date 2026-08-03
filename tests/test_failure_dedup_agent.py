"""Behavior examples for exact and LLM-owned Failure deduplication."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse, ToolCall
from restscope.operation_smoke.failure_dedup import (
    FailureDedupAgent,
    FailureDeduplicator,
    FailureDedupRequest,
)
from restscope.operation_smoke.memory import RecordedFailure, RecordedFailures


class StubClient:
    """Return scripted JSON decisions and retain model requests for inspection."""

    def __init__(self, responses: list[dict | LLMResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Return the next provider-neutral response."""
        self.requests.append(request)
        response = self.responses.pop(0)
        return (
            response
            if isinstance(response, LLMResponse)
            else LLMResponse(
                provider="stub",
                model="dedup-model",
                parsed_json=response,
            )
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


def _runtime():
    """Build the global OpenAPI Capability used by Dedup."""
    from restscope.capabilities import OpenAPICapability, ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Dedup", "version": "1"},
            "paths": {
                "/projects": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "namespace_id": {"type": "integer"},
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"201": {"description": "created"}},
                    }
                }
            },
        }
    )
    context = ToolContext(ir=ir, baseline_schema_source={})
    return OpenAPICapability(context_provider=lambda: context)


def _catalog(*cases: tuple[int, str, str]):
    """Record failed Catalog cases and return the run-local store."""
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(
        valid_parameters={"body", "body.name", "body.namespace_id"}
    )
    for status, message, name in cases:
        catalog.record(
            CatalogTestCaseDraft(
                parameters={"body.name": name},
                response_body={"message": message},
                failure=HTTPFailure(
                    status_code=status,
                    messages=[f"HTTP {status}: {message}"],
                ),
            )
        )
    return catalog


def _request(case_ids: list[str]) -> FailureDedupRequest:
    """Identify current-round failures by their run-local Catalog references."""
    return FailureDedupRequest(
        operation_key="POST /projects",
        round_number=1,
        batch_run_id="run-1",
        case_ids=case_ids,
        input_node_ids_by_handle={
            "body": "request/body",
            "body.name": "request/body/name",
            "body.namespace_id": "request/body/namespace_id",
        },
    )


def _agent(client: StubClient) -> FailureDedupAgent:
    """Build Dedup with the explicitly registered global OpenAPI Capability."""
    return FailureDedupAgent(
        client=client,
        model=_model(),
        openapi_capability=_runtime(),
    )


def test_exact_duplicate_bypasses_llm_and_keeps_first_test_case() -> None:
    """Formatting-identical messages need no semantic model decision."""
    client = StubClient([])
    memory = StubMemory()
    catalog = _catalog(
        (400, "name already exists", "first"),
        (400, "name already exists", "second"),
    )
    module = FailureDeduplicator(
        agent=_agent(client),
        memory=memory,
    )

    result = module.deduplicate(
        _request(["TC1", "TC2"]),
        catalog=catalog,
        max_outputs=3,
    )

    assert result.status == "bypassed"
    assert result.outputs_used == 0
    assert result.todos[0].test_case_id == "TC1"
    assert result.todos[0].suspected_parameters is None
    assert client.requests == []
    assert memory.writes[0].failures[0].messages == [
        "HTTP 400: name already exists"
    ]
    assert memory.writes[0].failures[0].suspected_input_node_ids is None


def test_agent_groups_by_parameter_and_each_failure_keeps_one_case() -> None:
    """Several messages may merge, but only the earliest case reaches Solve."""
    client = StubClient([
        LLMResponse(
            provider="stub",
            model="dedup-model",
            tool_calls=[
                ToolCall(
                    id="lookup-1",
                    name="openapi.list_inputs",
                    arguments={"operation_key": "POST /projects"},
                )
            ],
        ),
        LLMResponse(
            provider="stub",
            model="dedup-model",
            tool_calls=[
                ToolCall(
                    id="catalog-1",
                    name="test_case.get_parameter_value",
                    arguments={
                        "case_ids": ["TC1", "TC2"],
                        "parameter": "body.name",
                    },
                )
            ],
        ),
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
            },
    ])
    memory = StubMemory()
    catalog = _catalog(
        (400, "name already exists", "a"),
        (409, "name conflicts", "b"),
    )
    module = FailureDeduplicator(
        agent=_agent(client),
        memory=memory,
    )

    result = module.deduplicate(
        _request(["TC1", "TC2"]),
        catalog=catalog,
        max_outputs=4,
    )

    assert result.status == "deduplicated"
    assert result.outputs_used == 3
    assert len(result.todos) == 1
    assert result.todos[0].test_case_id == "TC1"
    assert result.todos[0].suspected_parameters == ["body.name"]
    assert sorted(memory.writes[0].failures[0].messages) == [
        "HTTP 400: name already exists",
        "HTTP 409: name conflicts",
    ]
    assert memory.writes[0].failures[0].suspected_input_node_ids == [
        "request/body/name"
    ]
    prompt = "\n".join(
        message.content or ""
        for message in client.requests[0].messages
        if message.content
    )
    assert "## Current Failure Cases — UNTRUSTED" in prompt
    assert "TC1" in prompt
    assert "HTTP 400: name already exists" in prompt
    assert "body.name" not in prompt
    assert "```json" not in prompt
    assert "fingerprint_ref" not in prompt.casefold()
    assert "item_id" not in prompt


def test_dedup_executes_multiple_independent_tool_calls_in_one_output() -> None:
    """Several Catalog reads run together and retain provider message order."""
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="dedup-model",
                tool_calls=[
                    ToolCall(
                        id="first-query",
                        name="test_case.get_parameter_value",
                        arguments={
                            "case_ids": ["TC1"],
                            "parameter": "body.name",
                        },
                    ),
                    ToolCall(
                        id="second-query",
                        name="test_case.get_failure_messages",
                        arguments={
                            "case_ids": ["TC2"],
                        },
                    ),
                ],
            ),
            {
                "failures": [
                    {
                        "summary": "Both failures concern the project name.",
                        "suspected_parameters": ["body.name"],
                        "messages": [
                            "HTTP 400: name already exists",
                            "HTTP 409: name conflicts",
                        ],
                    }
                ],
                "reason": "The independent queries confirm one Parameter group.",
            },
        ]
    )
    catalog = _catalog(
        (400, "name already exists", "a"),
        (409, "name conflicts", "b"),
    )

    decision, outputs, corrections, errors = _agent(client).deduplicate(
        operation_key="POST /projects",
        semantic_parameters=["body.name", "body.namespace_id"],
        observations=[
            {"case_id": "TC1", "message": "HTTP 400: name already exists"},
            {"case_id": "TC2", "message": "HTTP 409: name conflicts"},
        ],
        catalog=catalog,
        max_outputs=2,
    )

    assert decision is not None
    assert outputs == 2
    assert corrections == 0
    assert errors == []
    tool_results = [
        message
        for message in client.requests[1].messages
        if message.role == "tool"
    ]
    assert [message.tool_call_id for message in tool_results] == [
        "first-query",
        "second-query",
    ]


def test_dedup_registers_input_listing_and_five_test_case_tools() -> None:
    """Dedup receives one OpenAPI listing tool plus five exact evidence reads."""
    import json

    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="dedup-model",
                tool_calls=[
                    ToolCall(
                        id="current-operation",
                        name="openapi.list_inputs",
                        arguments={"operation_key": "POST /projects"},
                    )
                ],
            ),
            {
                "failures": [
                    {
                        "summary": "Both failures concern the project name.",
                        "suspected_parameters": ["body.name"],
                        "messages": [
                            "HTTP 400: name already exists",
                            "HTTP 409: name conflicts",
                        ],
                    }
                ],
                "reason": "The current operation declares body.name.",
            },
        ]
    )
    catalog = _catalog(
        (400, "name already exists", "a"),
        (409, "name conflicts", "b"),
    )

    decision, _outputs, corrections, errors = _agent(client).deduplicate(
        operation_key="POST /projects",
        semantic_parameters=["body.name", "body.namespace_id"],
        observations=[
            {"case_id": "TC1", "message": "HTTP 400: name already exists"},
            {"case_id": "TC2", "message": "HTTP 409: name conflicts"},
        ],
        catalog=catalog,
        max_outputs=2,
    )

    assert decision is not None
    assert corrections == 0
    assert errors == []
    openapi_spec = next(
        spec
        for spec in client.requests[0].tools
        if spec.name == "openapi.list_inputs"
    )
    assert openapi_spec.input_schema["required"] == ["operation_key"]
    assert {
        spec.name for spec in client.requests[0].tools if spec.name.startswith("openapi.")
    } == {"openapi.list_inputs"}
    assert {
        spec.name
        for spec in client.requests[0].tools
        if spec.name.startswith("test_case.")
    } == {
        "test_case.get_parameter_value",
        "test_case.find_parameters_by_value",
        "test_case.get_response_field_value",
        "test_case.find_response_fields_by_value",
        "test_case.get_failure_messages",
    }
    tool_message = next(
        message
        for message in client.requests[1].messages
        if message.role == "tool"
    )
    tool_result = json.loads(tool_message.content or "{}")
    assert tool_result["status"] == "succeeded"
    assert tool_result["structured"]["operation_key"] == "POST /projects"
    assert "schema" not in repr(tool_result["structured"])


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
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )

    catalog = TestCaseCatalog(
        valid_parameters={"body", "body.name", "body.namespace_id"}
    )
    catalog.record(
        CatalogTestCaseDraft(
            parameters={"body.name": "duplicate"},
            response_body={"message": {"name": ["has already been taken"]}},
            failure=HTTPFailure(
                status_code=400,
                messages=["HTTP 400: name: has already been taken"],
            ),
        )
    )
    catalog.record(
        CatalogTestCaseDraft(
            parameters={"body.namespace_id": 999},
            response_body={"message": {"namespace_id": ["is invalid"]}},
            failure=HTTPFailure(
                status_code=400,
                messages=["HTTP 400: namespace_id: is invalid"],
            ),
        )
    )
    module = FailureDeduplicator(
        agent=_agent(client),
        memory=memory,
    )

    result = module.deduplicate(
        _request(["TC1", "TC2"]),
        catalog=catalog,
        max_outputs=2,
    )

    assert result.status == "deduplicated"
    assert result.exact_fingerprint_count == 2
    assert len(client.requests) == 1
    assert [failure.messages for failure in memory.writes[0].failures] == [
        ["HTTP 400: name: has already been taken"],
        ["HTTP 400: namespace_id: is invalid"],
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
    catalog = _catalog(
        (400, "name already exists", "a"),
        (409, "name conflicts", "b"),
    )
    module = FailureDeduplicator(
        agent=_agent(client),
        memory=memory,
    )

    result = module.deduplicate(
        _request(["TC1", "TC2"]),
        catalog=catalog,
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
