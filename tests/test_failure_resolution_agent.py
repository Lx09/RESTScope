"""Protect the continuous reference-based Failure Resolution Agent session."""

from __future__ import annotations

from restscope.llm import LLMModelConfig, LLMResponse, ToolCall


class StubClient:
    """Return scripted model outputs while retaining every bounded request."""

    def __init__(self, responses):
        """Store the exact response sequence used by one test scenario."""
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Record one request and return the next scripted output."""
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Failure Resolution requested an unexpected output")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class StubOpenAPI:
    """Provide empty bounded lookup results without exposing operation context."""

    def list_inputs(self, **_arguments):
        """Return the empty lookup shape used only if a script calls the tool."""
        return {"structured": {"inputs": []}}

    def list_response_fields(self, **_arguments):
        """Return no response fields for focused session tests."""
        return {"structured": {"fields": []}}

    def get_input_schema(self, **_arguments):
        """Return one bounded placeholder input schema summary."""
        return {"structured": {"status": "not_found"}}

    def get_response_field_schema(self, **_arguments):
        """Return one bounded placeholder response schema summary."""
        return {"structured": {"status": "not_found"}}


class StubFinalizer:
    """Capture the final validated worklist without writing durable state."""

    def __init__(self):
        """Start with no attempted finalizations."""
        self.calls = []

    def finalize(self, **arguments):
        """Retain trusted inputs and report an empty successful commit summary."""
        from restscope.operation_smoke.failure_resolution import ResolutionCommit

        self.calls.append(arguments)
        return ResolutionCommit()


class StubMemory:
    """Return empty Parameter history for an exact current input node."""

    def __init__(self):
        """Retain requested operation/input pairs for ordering assertions."""
        self.calls = []

    def parameter_history(self, *, operation_key, input_node_id):
        """Return one valid empty history after recording the trusted identity."""
        from restscope.operation_smoke.memory import ParameterHistory

        self.calls.append((operation_key, input_node_id))
        return ParameterHistory(input_node_id=input_node_id)


class StubPatchCoordinator:
    """Spend two shared outputs and return one reviewed executable candidate."""

    def __init__(self):
        """Retain every semantic Patch task received from Resolution."""
        self.calls = []

    def run(self, *, task, output_limit, **_arguments):
        """Simulate Patch and Review outputs under the same Operation guard."""
        from restscope.operation_smoke.parameter_patch import (
            GeneratorPatchDraft,
            ValidatedParameterPatch,
        )
        from restscope.harness.testing import InputGeneratorPatch
        from restscope.harness.testing.models import ConstantGenerator

        self.calls.append(task)
        output_limit.consume("parameter_patch_agent")
        output_limit.consume("parameter_patch_review_agent")
        return ValidatedParameterPatch(
            todo_id=task.todo_id,
            patch=GeneratorPatchDraft(
                updates=[
                    InputGeneratorPatch(
                        input_node_id="path/projectId",
                        strategy=ConstantGenerator(
                            type="constant",
                            value="known-project",
                        ),
                    )
                ]
            ),
            samples=[
                {
                    "values": {"path.projectId": "known-project"},
                    "present": {"path.projectId": True},
                }
            ],
            outputs_used=2,
        )


class StubPatchFactory:
    """Return one reusable offline Coordinator for focused tool-loop tests."""

    def __init__(self):
        """Create the Coordinator whose calls the test will inspect."""
        self.coordinator = StubPatchCoordinator()

    def create(self):
        """Return the offline Coordinator without starting another Agent session."""
        return self.coordinator


class StubProbe:
    """Record every identical HTTP call as a fresh run-local Test Case."""

    def __init__(self):
        """Start with no target attempts or issued probe case references."""
        self.executed = []
        self.case_ids = []

    def binding(self, config):
        """Bind the canonical test Tool while execution remains intercepted."""
        from restscope.tools import ToolBinding

        del config
        return ToolBinding(
            name="restscope.http.request",
            execute=lambda **_arguments: {},
        )

    def validate(self, *, config, tool_call):
        """Accept only the current method and one concrete matching path."""
        if tool_call.arguments != {
            "method": config.snapshot.method.upper(),
            "path": "/projects/random-project",
        }:
            return "HTTP probe is outside the current operation"
        return None

    def execute(self, *, config, tool_call, catalog):
        """Record every call independently, even when its arguments repeat."""
        from restscope.llm import ToolResult
        from restscope.harness.testing.test_case_catalog import (
            CatalogTestCaseDraft,
            HTTPFailure,
        )

        self.executed.append(dict(tool_call.arguments))
        failure = HTTPFailure(
            status_code=400,
            messages=["HTTP 400: name is invalid"],
        )
        case = catalog.record(
            CatalogTestCaseDraft(
                request={
                    "path": {"projectId": "random-project"},
                    "query": {},
                    "header": {},
                    "cookie": {},
                },
                response_body={"message": "name is invalid"},
                failure=failure,
            )
        )
        self.case_ids.append(case.case_id)
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={
                "case_id": case.case_id,
                "status_code": 400,
                "failure": failure.model_dump(mode="json"),
            },
        )


def _model():
    """Build the single model role approved for Failure Resolution."""
    return LLMModelConfig(
        role="operation_smoke_failure_resolution",
        provider="stub",
        model="resolution-model",
        max_tokens=4_096,
        context_window_tokens=131_072,
    )


def _compact_model():
    """Build the FAST text role used only for Resolution history compaction."""
    return LLMModelConfig(
        role="operation_smoke_failure_resolution_compact",
        provider="stub",
        model="compact-model",
        max_tokens=4_096,
        context_window_tokens=131_072,
    )


def _catalog():
    """Create two failed cases with one exact repeated Failure message."""
    from restscope.harness.testing.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )
    from restscope.request_inputs import RequestInputReference

    catalog = TestCaseCatalog(
        input_references=[
            RequestInputReference.parameter("query", "name"),
            RequestInputReference.parameter("path", "projectId"),
            RequestInputReference.parameter("query", "region"),
        ]
    )
    for name in ("first", "second"):
        catalog.record(
            CatalogTestCaseDraft(
                request={
                    "path": {"projectId": "random-project"},
                    "query": {"name": name, "region": "us-east"},
                    "header": {},
                    "cookie": {},
                },
                response_body={"message": "name is invalid"},
                failure=HTTPFailure(
                    status_code=400,
                    messages=["HTTP 400: name is invalid"],
                ),
            )
        )
    return catalog


def _request():
    """Describe the failed Batch without embedding Test Cases or operation DTOs."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionRequest

    return FailureResolutionRequest(
        operation_key="POST /projects",
        round_number=1,
        batch_run_id="batch-1",
        case_ids=["TC1", "TC2"],
    )


def _write_call(*, call_id="write-1", active_item_id="WI-001"):
    """Replace the worklist with one item covering both exact associations."""
    from restscope.tools.worklist import WRITE_WORKLIST_TOOL_NAME

    return LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id=call_id,
                name=WRITE_WORKLIST_TOOL_NAME,
                arguments={
                    "expected_revision": 0,
                    "active_item_id": active_item_id,
                    "items": [
                        {
                            "item_id": "WI-001",
                            "source_failure_refs": ["E1"],
                            "test_case_refs": ["TC1", "TC2"],
                            "suspected_parameters": ["query.name"],
                            "progress": "The two exact failures are grouped.",
                            "root_cause": "Generated names violate the contract.",
                            "candidate_refs": [],
                            "decision": {
                                "outcome": "no_patch",
                                "selected_candidate_ref": None,
                                "reason": "Evidence does not support a safe Generator change.",
                            },
                        }
                    ],
                },
            )
        ],
    )


def _read_call(number):
    """Read the current worklist without changing the active item."""
    from restscope.tools.worklist import READ_WORKLIST_TOOL_NAME

    return LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id=f"read-{number}",
                name=READ_WORKLIST_TOOL_NAME,
                arguments={},
            )
        ],
    )


def _finish(reason="The worklist records the supported conclusions."):
    """Request mechanical final validation of the current worklist."""
    return LLMResponse(
        provider="stub",
        model="resolution-model",
        parsed_json={"reason": reason},
    )


def _run(responses, *, output_limit=None, openapi_capability=None):
    """Create and advance one Resolution session with offline collaborators."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    client = StubClient(responses)
    finalizer = StubFinalizer()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=openapi_capability or StubOpenAPI(),
        finalizer=finalizer,
    )
    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=output_limit or ModelOutputLimit(),
    ).advance()
    return outcome, client, finalizer


def test_one_continuous_session_groups_exact_sources_then_finishes() -> None:
    """Identical messages fold deterministically before one Agent-owned worklist."""
    outcome, client, finalizer = _run([_write_call(), _finish()])

    assert outcome.status == "completed"
    assert outcome.outputs_used == 2
    assert outcome.worklist.revision == 1
    assert len(finalizer.calls) == 1
    assert finalizer.calls[0]["sources"][0].model_dump(mode="json") == {
        "failure_ref": "E1",
        "message": "HTTP 400: name is invalid",
        "test_case_refs": ["TC1", "TC2"],
    }

    initial = client.requests[0].messages[1].content
    assert "POST /projects" in initial
    assert "HTTP 400: name is invalid" in initial
    assert "TC1" in initial and "TC2" in initial
    assert "round_number" not in initial
    assert "batch-1" not in initial
    assert "query.name" not in initial
    assert "first" not in initial and "second" not in initial
    assert client.requests[0].metadata == {
        "role": "operation_smoke_failure_resolution"
    }
    tool_names = {tool.name for tool in client.requests[0].tools}
    assert "openapi.list_response_fields" in tool_names
    assert "test_case.get_response_field_value" in tool_names
    assert "test_case.get_failure_messages" not in tool_names
    system = client.requests[0].messages[0].content
    assert "Failure messages are already in the initial user prompt" in system
    assert "openapi.list_response_fields" in system
    assert "test_case.get_response_field_value" in system
    assert "Failure Resolution context checkpoint" in system
    assert "return Markdown only" in system
    assert "Assign WI-001" in system
    assert "Never reuse a\n  deleted ID" in system
    assert "When splitting an item, keep its ID" in system
    assert "When merging items, keep the earliest ID" in system
    assert "WI-1000" in system


def test_resolution_compacts_b_plus_h_into_b_plus_h_prime_at_eighty_percent() -> None:
    """A large Resolution prompt is replaced by U plus S before the next turn."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.failure_resolution.compact import (
        COMPACT_INSTRUCTION,
    )
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    first_resolution = _write_call()
    first_resolution.prompt_tokens = 102_000
    compact_summary = (
        "# Failure Resolution checkpoint\n\n"
        "E1 is covered by WI-001 and has a no_patch decision."
    )
    client = StubClient(
        [
            first_resolution,
            LLMResponse(
                provider="stub",
                model="compact-model",
                content=compact_summary,
            ),
            _finish(),
        ]
    )
    finalizer = StubFinalizer()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=finalizer,
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
    ).advance()

    assert outcome.status == "completed"
    assert outcome.outputs_used == 3
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution",
    ]

    first_system = client.requests[0].messages[0].content
    compact_request = client.requests[1]
    assert compact_request.messages[0].content == first_system
    assert compact_request.messages[-1].content == COMPACT_INSTRUCTION
    assert any(
        message.role == "tool"
        and message.name == "failure_resolution.write_worklist"
        for message in compact_request.messages
    )

    messages_after_compact = client.requests[2].messages
    assert messages_after_compact[0].content == first_system
    assert messages_after_compact[1].content == client.requests[0].messages[1].content
    assert messages_after_compact[2].role == "user"
    assert "Another Failure Resolution model" in messages_after_compact[2].content
    assert compact_summary in messages_after_compact[2].content
    assert all(message.role != "tool" for message in messages_after_compact)
    assert all(
        message.content != COMPACT_INSTRUCTION
        for message in messages_after_compact
    )
    assert outcome.worklist.revision == 1
    assert len(finalizer.calls) == 1


def test_resolution_does_not_compact_below_eighty_percent() -> None:
    """Provider usage below the configured waterline keeps the current H."""
    first_resolution = _write_call()
    first_resolution.prompt_tokens = 50_000

    outcome, client, _finalizer = _run([first_resolution, _finish()])

    assert outcome.status == "completed"
    assert outcome.outputs_used == 2
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution",
    ]
    assert any(
        message.role == "tool"
        and message.name == "failure_resolution.write_worklist"
        for message in client.requests[-1].messages
    )


def test_resolution_clips_only_the_compact_summary_and_keeps_handoff_prefix() -> None:
    """An oversized S keeps its head and tail without rewriting the fixed prefix."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    first_resolution = _write_call()
    first_resolution.prompt_tokens = 102_000
    compact_summary = "SUMMARY-HEAD\n" + ("x" * 30_000) + "\nSUMMARY-TAIL"
    client = StubClient(
        [
            first_resolution,
            LLMResponse(
                provider="stub",
                model="compact-model",
                content=compact_summary,
            ),
            _finish(),
        ]
    )
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=StubFinalizer(),
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
    ).advance()

    assert outcome.status == "completed"
    handoff = client.requests[-1].messages[2].content
    assert handoff.startswith(
        "Another Failure Resolution model previously investigated this operation."
    )
    assert "SUMMARY-HEAD" in handoff
    assert "SUMMARY-TAIL" in handoff
    assert len(handoff.rsplit("\n\n", maxsplit=1)[-1]) == 24_000


def test_resolution_uses_full_request_bytes_when_prompt_usage_is_missing() -> None:
    """A provider without usage still triggers Compact from a safe upper bound."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    small_resolution_model = _model().model_copy(
        update={"max_tokens": 1_024, "context_window_tokens": 8_192}
    )
    compact_summary = "# Checkpoint\n\nContinue from worklist revision 1."
    client = StubClient(
        [
            _write_call(),
            LLMResponse(
                provider="stub",
                model="compact-model",
                content=compact_summary,
            ),
            _finish(),
        ]
    )
    agent = FailureResolutionAgent(
        client=client,
        model=small_resolution_model,
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=StubFinalizer(),
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
    ).advance()

    assert outcome.status == "completed"
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution",
    ]


def test_second_compact_absorbs_the_old_summary_without_preserving_it_as_u() -> None:
    """Repeated compaction keeps U once and replaces S1 with the newer S2."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    first_resolution = _write_call()
    first_resolution.prompt_tokens = 102_000
    second_resolution = _read_call(2)
    second_resolution.prompt_tokens = 102_000
    first_summary = "# First checkpoint\n\nE1 decision exists."
    second_summary = "# Second checkpoint\n\nE1 remains decided after rereading revision 1."
    client = StubClient(
        [
            first_resolution,
            LLMResponse(
                provider="stub",
                model="compact-model",
                content=first_summary,
            ),
            second_resolution,
            LLMResponse(
                provider="stub",
                model="compact-model",
                content=second_summary,
            ),
            _finish(),
        ]
    )
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=StubFinalizer(),
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
    ).advance()

    assert outcome.status == "completed"
    assert outcome.outputs_used == 5
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution",
    ]
    assert any(
        first_summary in message.content
        for message in client.requests[3].messages
    )
    final_messages = client.requests[-1].messages
    assert final_messages[1].content == client.requests[0].messages[1].content
    assert any(second_summary in message.content for message in final_messages)
    assert not any(first_summary in message.content for message in final_messages)
    assert all(message.role != "tool" for message in final_messages)


def test_resolution_keeps_h_and_disables_compact_after_two_provider_failures() -> None:
    """A broken Compact model cannot erase H or block mechanical continuation."""
    from restscope.llm import ProviderInvokeError
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    first_resolution = _write_call()
    first_resolution.prompt_tokens = 102_000
    client = StubClient(
        [
            first_resolution,
            ProviderInvokeError("first compact failure"),
            ProviderInvokeError("second compact failure"),
            _finish(),
        ]
    )
    finalizer = StubFinalizer()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=finalizer,
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
    ).advance()

    assert outcome.status == "completed"
    assert outcome.outputs_used == 4
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution_compact",
        "operation_smoke_failure_resolution",
    ]
    final_messages = client.requests[-1].messages
    assert any(
        message.role == "tool"
        and message.name == "failure_resolution.write_worklist"
        for message in final_messages
    )
    assert not any(
        "Another Failure Resolution model" in message.content
        for message in final_messages
    )
    assert len(finalizer.calls) == 1


def test_compact_retry_respects_the_shared_operation_output_limit() -> None:
    """The second Compact attempt cannot bypass the single 1000-output guard."""
    from restscope.llm import ProviderInvokeError
    from restscope.operation_smoke.failure_resolution import FailureResolutionAgent
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    first_resolution = _write_call()
    first_resolution.prompt_tokens = 102_000
    client = StubClient(
        [
            first_resolution,
            ProviderInvokeError("first compact failure"),
        ]
    )
    finalizer = StubFinalizer()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=finalizer,
    )

    outcome = agent.start(
        _request(),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(max_outputs=2),
    ).advance()

    assert outcome.status == "failure_resolution_limit_exceeded"
    assert outcome.outputs_used == 2
    assert [request.metadata["role"] for request in client.requests] == [
        "operation_smoke_failure_resolution",
        "operation_smoke_failure_resolution_compact",
    ]
    assert finalizer.calls == []


def test_unclear_failure_can_follow_schema_fields_to_exact_case_values() -> None:
    """Resolution can discover a response path, then inspect its failed TC value."""

    class ResponseFieldOpenAPI(StubOpenAPI):
        """Return one response path for the current operation and status."""

        def __init__(self):
            """Start without any field-discovery requests."""
            self.calls = []

        def list_response_fields(self, **arguments):
            """Record the exact lookup and return a contract field candidate."""
            self.calls.append(arguments)
            return {
                "structured": {
                    "operation_key": arguments["operation_key"],
                    "requested_status_code": "400",
                    "matched_status_code": "400",
                    "media_type": "application/json",
                    "fields": [{"name": "body.message"}],
                    "total": 1,
                    "offset": 0,
                }
            }

    list_fields = LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id="list-response-fields",
                name="openapi.list_response_fields",
                arguments={
                    "operation_key": "POST /projects",
                    "status_code": 400,
                },
            )
        ],
    )
    get_value = LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id="get-response-value",
                name="test_case.get_response_field_value",
                arguments={
                    "case_ids": ["TC1", "TC2"],
                    "field": "body.message",
                },
            )
        ],
    )
    openapi = ResponseFieldOpenAPI()

    outcome, client, _finalizer = _run(
        [list_fields, get_value, _write_call(), _finish()],
        openapi_capability=openapi,
    )

    assert outcome.status == "completed"
    assert openapi.calls == [
        {"operation_key": "POST /projects", "status_code": 400}
    ]
    field_discovery = [
        message.content
        for message in client.requests[1].messages
        if message.role == "tool" and message.name == "openapi.list_response_fields"
    ][0]
    assert "body.message" in field_discovery
    case_evidence = [
        message.content
        for message in client.requests[2].messages
        if message.role == "tool"
        and message.name == "test_case.get_response_field_value"
    ][0]
    assert '"message":"name is invalid"' in case_evidence


def test_incomplete_finish_returns_coverage_error_to_the_same_session() -> None:
    """A premature finish is corrected in place instead of spawning a new Agent."""
    outcome, client, finalizer = _run([_finish(), _write_call(), _finish()])

    assert outcome.status == "completed"
    assert outcome.outputs_used == 3
    corrections = [
        message.content
        for message in client.requests[1].messages
        if message.role == "user"
    ]
    assert any("missing source evidence" in text for text in corrections)
    assert len(finalizer.calls) == 1


def test_invalid_patch_decision_is_corrected_in_the_same_session() -> None:
    """A schema-rejected Patch choice can be rewritten without losing the session."""
    invalid = _write_call()
    invalid.tool_calls[0].arguments["items"][0]["decision"] = {
        "outcome": "apply_patch",
        "reason": "The candidate passed validation.",
    }
    corrected = _write_call(call_id="write-2", active_item_id=None)

    outcome, client, finalizer = _run([invalid, corrected, _finish()])

    assert outcome.status == "completed"
    assert outcome.outputs_used == 3
    assert outcome.worklist.revision == 1
    assert len(finalizer.calls) == 1
    rejected_feedback = [
        message.content
        for message in client.requests[1].messages
        if message.role == "tool"
        and message.name == "failure_resolution.write_worklist"
    ]
    assert len(rejected_feedback) == 1
    assert "invalid_tool_arguments" in rejected_feedback[0]
    assert "internal_tool_error" not in rejected_feedback[0]

    first_request = client.requests[0]
    system_prompt = first_request.messages[0].content
    write_tool = next(
        tool
        for tool in first_request.tools
        if tool.name == "failure_resolution.write_worklist"
    )
    for contract_text in (system_prompt, write_tool.description):
        assert "selected_candidate_ref" in contract_text
        assert "candidate_refs" in contract_text


def test_worklist_write_cannot_share_one_model_output_with_another_tool() -> None:
    """The stateful replacement is rejected as a whole before any tool executes."""
    mixed = _write_call()
    mixed.tool_calls.append(
        ToolCall(
            id="read-mixed",
            name="failure_resolution.read_worklist",
            arguments={},
        )
    )
    outcome, client, _finalizer = _run([mixed, _write_call(), _finish()])

    assert outcome.status == "completed"
    assert outcome.worklist.revision == 1
    corrections = [
        message.content
        for message in client.requests[1].messages
        if message.role == "user"
    ]
    assert any("worklist write" in text.lower() for text in corrections)


def test_eleventh_active_item_output_adds_progressive_budget_feedback() -> None:
    """Only a long-running active item receives the approved urgency reminder."""
    responses = [_write_call()] + [_read_call(index) for index in range(1, 12)] + [
        _finish()
    ]
    outcome, client, _finalizer = _run(responses)

    assert outcome.status == "completed"
    final_request_feedback = [
        message.content
        for message in client.requests[-1].messages
        if message.role == "user"
    ]
    assert any("当前 Failure 已处理 11 轮" in text for text in final_request_feedback)
    assert any("当前剩余 988" in text for text in final_request_feedback)


def test_shared_limit_stops_before_an_unreserved_provider_call() -> None:
    """Exhaustion returns the Resolution failure kind without finalization writes."""
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    outcome, client, finalizer = _run(
        [_read_call(1)],
        output_limit=ModelOutputLimit(max_outputs=1),
    )

    assert outcome.status == "failure_resolution_limit_exceeded"
    assert outcome.outputs_used == 1
    assert len(client.requests) == 1
    assert finalizer.calls == []


def test_patch_tool_returns_only_p_ref_while_registry_keeps_exact_candidate() -> None:
    """Patch and Review share the guard, and no executable DTO returns to the model."""
    from restscope.llm import ToolCall
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionAgent,
        FailureResolutionRequest,
    )
    from restscope.operation_smoke.output_limit import ModelOutputLimit
    from tests._operation_smoke_resolution_fixtures import smoke_config

    initial = _write_call()
    initial.tool_calls[0].arguments["items"][0]["suspected_parameters"] = [
        "path.projectId"
    ]
    initial.tool_calls[0].arguments["items"][0]["decision"] = None
    memory = LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id="memory-1",
                name="lookup_parameter_history",
                arguments={"input_handles": ["path.projectId"]},
            )
        ],
    )
    patch = LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id="patch-1",
                name="generate_parameter_patch",
                arguments={
                    "root_cause": "Random project identifiers do not exist.",
                    "affected_inputs": ["path.projectId"],
                    "value_requirements": "Use an existing project identifier.",
                    "acceptance_criteria": [
                        "path.projectId is an existing identifier."
                    ],
                },
            )
        ],
    )
    final_write = _write_call(call_id="write-2", active_item_id=None)
    final_write.tool_calls[0].arguments["expected_revision"] = 1
    item = final_write.tool_calls[0].arguments["items"][0]
    item["suspected_parameters"] = ["path.projectId"]
    item["candidate_refs"] = ["P1"]
    item["decision"] = {
        "outcome": "apply_patch",
        "selected_candidate_ref": "P1",
        "reason": "P1 is the reviewed repair.",
    }
    client = StubClient([initial, memory, patch, final_write, _finish()])
    finalizer = StubFinalizer()
    patch_factory = StubPatchFactory()
    output_limit = ModelOutputLimit()
    current = smoke_config()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=finalizer,
        memory=StubMemory(),
        patch_coordinator_factory=patch_factory,
    )

    outcome = agent.start(
        FailureResolutionRequest(
            operation_key=current.operation_key,
            round_number=1,
            batch_run_id="batch-1",
            case_ids=["TC1", "TC2"],
        ),
        catalog=_catalog(),
        output_limit=output_limit,
        config=current,
        active_constraints=[],
    ).advance()

    assert outcome.status == "completed"
    assert outcome.outputs_used == 7
    assert output_limit.used == 7
    assert output_limit.consume  # The same instance remains the single guard.
    assert patch_factory.coordinator.calls[0].todo_id == "WI-001"
    precise = finalizer.calls[0]["candidates"].get("P1")
    assert precise.patch.updates[0].strategy.model_dump(mode="json") == {
        "type": "constant",
        "value": "known-project",
    }
    patch_feedback = [
        message.content
        for message in client.requests[3].messages
        if message.role == "tool" and message.name == "generate_parameter_patch"
    ][0]
    assert "P1" in patch_feedback
    assert "known-project" not in patch_feedback
    patch_schema = {
        tool.name: tool for tool in client.requests[0].tools
    }["generate_parameter_patch"].input_schema
    assert "enum" not in patch_schema["properties"]["affected_inputs"]["items"]


def test_identical_http_probes_execute_twice_and_issue_fresh_case_refs() -> None:
    """No repeated-tool heuristic suppresses target calls or their side effects."""
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionAgent,
        FailureResolutionRequest,
    )
    from restscope.operation_smoke.output_limit import ModelOutputLimit
    from tests._operation_smoke_resolution_fixtures import smoke_config

    def probe_output(number):
        """Build one identical probe request with a provider-unique call ID."""
        return LLMResponse(
            provider="stub",
            model="resolution-model",
            tool_calls=[
                ToolCall(
                    id=f"probe-{number}",
                    name="restscope.http.request",
                    arguments={
                        "method": "GET",
                        "path": "/projects/random-project",
                    },
                )
            ],
        )

    current = smoke_config()
    probe = StubProbe()
    client = StubClient([_write_call(), probe_output(1), probe_output(2), _finish()])
    finalizer = StubFinalizer()
    catalog = _catalog()
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=finalizer,
        http_probe=probe,
    )

    outcome = agent.start(
        FailureResolutionRequest(
            operation_key=current.operation_key,
            round_number=1,
            batch_run_id="batch-1",
            case_ids=["TC1", "TC2"],
        ),
        catalog=catalog,
        output_limit=ModelOutputLimit(),
        config=current,
    ).advance()

    assert outcome.status == "completed"
    assert len(probe.executed) == 2
    assert probe.executed[0] == probe.executed[1]
    assert probe.case_ids == ["TC3", "TC4"]
    assert finalizer.calls[0]["sources"][0].test_case_refs == [
        "TC1",
        "TC2",
        "TC3",
        "TC4",
    ]
    probe_feedback = [
        message
        for request in client.requests[2:4]
        for message in request.messages
        if message.role == "tool" and message.name == "restscope.http.request"
    ]
    assert any("TC3" in message.content for message in probe_feedback)
    assert any("TC4" in message.content for message in probe_feedback)
    assert not any(tool.strict for tool in client.requests[0].tools)


def test_http_probe_requires_an_active_worklist_item() -> None:
    """Target mutation cannot begin before Resolution names its current item."""
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionAgent,
        FailureResolutionRequest,
    )
    from restscope.operation_smoke.output_limit import ModelOutputLimit
    from tests._operation_smoke_resolution_fixtures import smoke_config

    current = smoke_config()
    early_probe = LLMResponse(
        provider="stub",
        model="resolution-model",
        tool_calls=[
            ToolCall(
                id="probe-before-worklist",
                name="restscope.http.request",
                arguments={
                    "method": "GET",
                    "path": "/projects/random-project",
                },
            )
        ],
    )
    probe = StubProbe()
    client = StubClient([early_probe, _write_call(), _finish()])
    agent = FailureResolutionAgent(
        client=client,
        model=_model(),
        compact_model=_compact_model(),
        openapi_capability=StubOpenAPI(),
        finalizer=StubFinalizer(),
        http_probe=probe,
    )

    outcome = agent.start(
        FailureResolutionRequest(
            operation_key=current.operation_key,
            round_number=1,
            batch_run_id="batch-1",
            case_ids=["TC1", "TC2"],
        ),
        catalog=_catalog(),
        output_limit=ModelOutputLimit(),
        config=current,
    ).advance()

    assert outcome.status == "completed"
    assert probe.executed == []
    assert any(
        "Set active_item_id" in message.content
        for message in client.requests[1].messages
        if message.role == "user"
    )


def test_long_exact_failure_stays_in_registry_while_prompt_is_bounded() -> None:
    """Large target text must not break E refs or enter the prompt unbounded."""
    from restscope.operation_smoke.failure_resolution import build_failure_sources
    from restscope.operation_smoke.failure_resolution.prompts import (
        failure_source_prompt,
    )
    from restscope.harness.testing.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )
    from restscope.request_inputs import RequestInputReference

    exact_message = "HTTP 400: " + ("invalid field; " * 300) + "END"
    catalog = TestCaseCatalog(
        input_references=[RequestInputReference.parameter("query", "name")]
    )
    catalog.record(
        CatalogTestCaseDraft(
            request={
                "path": {},
                "query": {"name": "bad"},
                "header": {},
                "cookie": {},
            },
            response_body={"message": "large validation response"},
            failure=HTTPFailure(status_code=400, messages=[exact_message]),
        )
    )

    sources = build_failure_sources(catalog=catalog, case_ids=["TC1"])
    rendered = failure_source_prompt(
        operation_key="GET /projects",
        sources=sources,
    )

    assert sources[0].message == exact_message
    assert len(rendered.text) < len(exact_message)
    assert exact_message not in rendered.text
    assert "clipped from" in rendered.text
