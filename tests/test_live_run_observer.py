"""Behavior contracts for the schema-v3 semantic run observer.

These scenarios exercise the observer through the same tracing and target HTTP
Interfaces used by production. They protect the user-visible meaning of Agent,
and Tool cards without treating Phoenix's lower-level span model
as the browser contract.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Response:
    """Provide the bounded target response fields consumed by the observer."""

    status_code: int
    reason_phrase: str
    url: str
    headers: dict[str, str]
    body: bytes | None
    body_truncated: bool = False
    encoding: str = "utf-8"
    processor_result: object | None = None


def test_live_observer_emits_only_agent_and_tool_cards_without_phoenix() -> None:
    """Scenario: the UI remains semantic and complete with Phoenix disabled."""
    from restscope.observability import LiveRunObserver, Redactor, TracingRuntime

    observer = LiveRunObserver(redactor=Redactor(["model-secret"]))
    observer.begin_run({"metadata": {"key": "model-secret"}})
    runtime = TracingRuntime(redactor=Redactor(["model-secret"]), run_observer=observer)

    with runtime.span(
        "RESTScopeApp.start",
        kind="CHAIN",
    ), runtime.span(
        "main",
        kind="AGENT",
        attributes={"restscope.operation.key": "GET /projects"},
    ) as agent_span:
        agent_span.set_live_detail(
            "failure_messages",
            {"E1": "HTTP 400: name already exists"},
        )
        with runtime.span(
            "LLMClient.invoke",
            kind="LLM",
            attributes={
                "restscope.llm.role": "failure_resolution_agent",
                "llm.model_name": "thinking-model",
            },
        ) as model_span:
            model_span.set_llm_input_messages(
                [
                    {"role": "system", "content": "Keep model-secret safe"},
                    {"role": "user", "content": "Diagnose E1"},
                ]
            )
            model_span.set_llm_output_messages(
                [{"role": "assistant", "content": "Create one work item"}],
                summary={"finish_reason": "tool_calls"},
            )

        with runtime.span(
            "diagnosis.record",
            kind="TOOL",
            input_value={"arguments": {"expected_revision": 0}},
        ) as tool_span:
            tool_span.set_output(
                {
                    "tool_call_id": "call-1",
                    "name": "diagnosis.record",
                    "status": "succeeded",
                    "structured": {
                        "revision": 1,
                        "active_item_id": "WI-001",
                        "items": [
                            {
                                "item_id": "WI-001",
                                "source_failure_refs": ["E1"],
                                "test_case_refs": ["TC1"],
                                "suspected_parameters": [],
                                "progress": "Investigating",
                                "root_cause": None,
                                "candidate_refs": [],
                                "decision": None,
                            }
                        ],
                    },
                }
            )

    snapshot = observer.snapshot()

    assert snapshot["schema_version"] == 3
    assert [event["kind"] for event in snapshot["events"]] == [
        "agent_turn",
        "tool_call",
    ]
    turn, tool = snapshot["events"]
    assert turn["name"] == "main"
    assert turn["detail"]["input"]["messages"][0]["content"] == (
        "Keep ***REDACTED*** safe"
    )
    assert turn["detail"]["output"]["content"] == "Create one work item"
    assert tool["detail"]["input"] == {"arguments": {"expected_revision": 0}}
    assert tool["detail"]["output"]["status"] == "succeeded"
    # An ordinary domain Tool does not become the Main Plan merely because the
    # payload happens to contain similarly shaped working data.
    assert snapshot["todo"] is None
    assert "model-secret" not in str(snapshot)


def test_main_agent_plan_update_projects_the_generic_todo() -> None:
    """A successful Main Agent plan.update is the only floating Todo source."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span(
        "Agent.run",
        kind="CHAIN",
        input_value={"objective": "Inspect the API"},
        attributes={
            "restscope.agent.session_id": "main-1",
            "restscope.agent.profile": "main_profile",
            "restscope.agent.lifecycle": "main",
        },
    ), runtime.span("plan.update", kind="TOOL") as tool_span:
        tool_span.set_output(
            {
                "status": "succeeded",
                "structured": {
                    "explanation": "Follow the evidence in order.",
                    "plan": [
                        {"step": "Read the schema", "status": "completed"},
                        {"step": "Probe the endpoint", "status": "in_progress"},
                        {"step": "Report findings", "status": "pending"},
                    ],
                },
            }
        )

    snapshot = observer.snapshot()

    assert snapshot["todo"] == {
        "revision": 1,
        "agent": snapshot["events"][0]["agent"],
        "explanation": "Follow the evidence in order.",
        "items": [
            {"step": "Read the schema", "status": "completed"},
            {"step": "Probe the endpoint", "status": "in_progress"},
            {"step": "Report findings", "status": "pending"},
        ],
        "completed_count": 1,
        "total_count": 3,
        "active_step": "Probe the endpoint",
        "percent": 33,
    }
    assert observer.wait_after(0)[-1]["type"] == "todo.replace"


def test_generic_agent_task_exposes_identity_reasoning_and_validated_final_phase() -> None:
    """Only a completed generic Agent task promotes its last turn to Final Answer."""
    from restscope.observability import LiveRunObserver, Redactor, TracingRuntime

    observer = LiveRunObserver(redactor=Redactor(["private-token"]))
    observer.begin_run({})
    runtime = TracingRuntime(
        redactor=Redactor(["private-token"]),
        run_observer=observer,
    )

    with runtime.span(
        "Agent.run",
        kind="CHAIN",
        input_value={"objective": "Inspect private-token safely"},
        attributes={
            "restscope.agent.session_id": "main-1",
            "restscope.agent.profile": "main_profile",
            "restscope.agent.depth": 0,
            "restscope.agent.lifecycle": "main",
        },
    ) as task_span:
        with runtime.span("LLMClient.invoke", kind="LLM") as model_span:
            model_span.set_llm_input_messages(
                [{"role": "user", "content": "Inspect private-token safely"}]
            )
            model_span.set_live_detail(
                "reasoning",
                "private-token requires a careful lookup",
            )
            model_span.set_llm_output_messages(
                [{"role": "assistant", "content": '{"summary":"done"}'}],
                summary={"parsed_json": {"summary": "done"}, "finish_reason": "stop"},
            )
        task_span.set_output(
            {
                "session_id": "main-1",
                "profile_name": "main_profile",
                "status": "completed",
                "completion": {"summary": "done"},
            }
        )

    event = observer.snapshot()["events"][0]

    assert event["agent"] == {
        "session_id": "main-1",
        "parent_session_id": None,
        "name": "main_profile",
        "profile_name": "main_profile",
        "lifecycle": "main",
        "task_id": event["agent"]["task_id"],
        "path": ["main_profile"],
    }
    assert event["detail"]["task"]["objective"] == (
        "Inspect ***REDACTED*** safely"
    )
    assert event["detail"]["reasoning"] == (
        "***REDACTED*** requires a careful lookup"
    )
    assert event["detail"]["phase"] == "final_answer"
    assert event["detail"]["task_result"]["status"] == "completed"


def test_failed_generic_agent_task_never_marks_a_final_answer() -> None:
    """A candidate rejected by Agent validation remains ordinary commentary."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span(
        "Agent.run",
        kind="CHAIN",
        input_value={"objective": "Return a result"},
        attributes={
            "restscope.agent.session_id": "child-1",
            "restscope.agent.parent_session_id": "main-1",
            "restscope.agent.profile": "child_profile",
            "restscope.agent.lifecycle": "subagent",
        },
    ) as task_span:
        with runtime.span("LLMClient.invoke", kind="LLM") as model_span:
            model_span.set_llm_output_messages(
                [{"role": "assistant", "content": "invalid"}],
                summary={"finish_reason": "stop"},
            )
        task_span.set_output(
            {
                "session_id": "child-1",
                "profile_name": "child_profile",
                "status": "failed",
                "error": {"code": "invalid", "message": "invalid"},
            }
        )

    event = observer.snapshot()["events"][0]
    assert event["agent"]["lifecycle"] == "subagent"
    assert event["agent"]["parent_session_id"] == "main-1"
    assert event["detail"]["phase"] == "commentary"


def test_system_agent_is_an_independent_root_displayed_under_its_http_tool() -> None:
    """Causal UI nesting does not invent Subagent parentage."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with (
        runtime.span("main", kind="AGENT"),
        runtime.span("restscope.http.request", kind="TOOL") as http_span,
    ):
        with runtime.span(
                "Agent.run",
                kind="CHAIN",
                input_value={"objective": "Choose I1"},
                attributes={
                    "restscope.agent.session_id": "system-1",
                    "restscope.agent.profile": "resource-identifier-selector",
                    "restscope.agent.lifecycle": "system",
                },
        ) as system_span:
            with runtime.span("LLMClient.invoke", kind="LLM") as model_span:
                model_span.set_llm_input_messages(
                    [{"role": "user", "content": "Choose I1"}]
                )
                model_span.set_llm_output_messages(
                    [{"role": "assistant", "content": '{"identifier":"I1"}'}]
                )
            system_span.set_output(
                {
                    "session_id": "system-1",
                    "profile_name": "resource-identifier-selector",
                    "status": "completed",
                    "output": {"identifier": "I1"},
                }
            )
        http_span.set_output({"status": "succeeded"})

    http_event, system_turn = observer.snapshot()["events"]

    assert system_turn["parent_event_id"] == http_event["event_id"]
    assert system_turn["agent"]["lifecycle"] == "system"
    assert system_turn["agent"]["parent_session_id"] is None
    assert system_turn["detail"]["phase"] == "final_answer"


def test_llm_client_routes_reasoning_only_to_the_redacted_live_observer() -> None:
    """The shared client emits Reasoning through observer detail, not prompt data."""
    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
    )
    from restscope.llm.providers.base import BaseLLMProvider
    from restscope.observability import LiveRunObserver, Redactor, TracingRuntime

    class ReasoningProvider(BaseLLMProvider):
        """Return one raw reasoning value without changing request messages."""

        name = "reasoning_stub"

        def invoke(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model=request.model,
                content="done",
                reasoning_content="secret-thought then inspect schema",
                finish_reason="stop",
            )

    observer = LiveRunObserver(redactor=Redactor(["secret-thought"]))
    observer.begin_run({})
    runtime = TracingRuntime(
        redactor=Redactor(["secret-thought"]),
        run_observer=observer,
    )
    registry = LLMProviderRegistry()
    registry.register(ReasoningProvider())
    client = LLMClient(registry, tracing_runtime=runtime)

    with runtime.span("LegacyAgent.run", kind="AGENT"):
        client.invoke(
            LLMRequest(
                provider="reasoning_stub",
                model="reasoning-model",
                messages=[LLMMessage(role="user", content="Inspect the schema")],
            )
        )

    detail = observer.snapshot()["events"][0]["detail"]
    assert detail["reasoning"] == "***REDACTED*** then inspect schema"
    assert detail["input"]["messages"][0]["content"] == "Inspect the schema"
    assert "reasoning" not in detail["input"]["messages"][0]


def test_agent_session_produces_incremental_turn_cards_with_exact_outputs() -> None:
    """Scenario: later turns contain every new message and no repeated history."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span("patch", kind="AGENT"):
        first_prompt = [
            {"role": "system", "content": "Patch safely"},
            {"role": "user", "content": "Fix query.sort"},
        ]
        assistant = {
            "role": "assistant",
            "content": "Need both lookups",
            "tool_calls": [
                {
                    "id": "call-schema",
                    "name": "openapi.get_input_schema",
                    "arguments": {"handle": "query.sort"},
                },
                {
                    "id": "call-memory",
                    "name": "request_generation.get_input_state",
                    "arguments": {"input_handles": ["query.sort"]},
                },
            ],
        }
        with runtime.span("LLMClient.invoke", kind="LLM") as span:
            span.set_llm_input_messages(first_prompt)
            span.set_llm_output_messages(
                [assistant],
                summary={
                    "parsed_json": {"note": "structured intent"},
                    "finish_reason": "tool_calls",
                },
            )
        with runtime.span("LLMClient.invoke", kind="LLM") as span:
            span.set_llm_input_messages(
                [
                    *first_prompt,
                    assistant,
                    {
                        "role": "tool",
                        "name": "openapi.get_input_schema",
                        "tool_call_id": "call-schema",
                        "content": "string enum",
                    },
                    {
                        "role": "tool",
                        "name": "request_generation.get_input_state",
                        "tool_call_id": "call-memory",
                        "content": "no prior values",
                    },
                    {
                        "role": "user",
                        "content": "Harness feedback: cover both constraints",
                    },
                ]
            )
            span.set_llm_output_messages(
                [{"role": "assistant", "content": "Ready", "tool_calls": []}],
                summary={
                    "parsed_json": {"proposal": "ready"},
                    "finish_reason": "stop",
                },
            )

    turns = observer.snapshot()["events"]

    assert [event["kind"] for event in turns] == ["agent_turn", "agent_turn"]
    assert [message["role"] for message in turns[0]["detail"]["input"]["messages"]] == [
        "system",
        "user",
    ]
    assert [message["role"] for message in turns[1]["detail"]["input"]["messages"]] == [
        "tool",
        "tool",
        "user",
    ]
    assert turns[0]["detail"]["output"] == {
        "messages": [assistant],
        "content": "Need both lookups",
        "structured": {"note": "structured intent"},
        "finish_reason": "tool_calls",
        "tool_calls": assistant["tool_calls"],
    }
    assert turns[1]["detail"]["output"]["structured"] == {"proposal": "ready"}
    assert all("llm.model_name" not in event["attributes"] for event in turns)


def test_nested_agent_identity_keeps_its_direct_parent_session() -> None:
    """Scenario: a direct nested Agent remains attributable without a visible parent event."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    # The child Agent starts after the parent's model turn has closed, so the
    # Agent relationship remains while no visible turn becomes its UI parent.
    with runtime.span("main", kind="AGENT"):
        with runtime.span("LLMClient.invoke", kind="LLM") as parent_turn:
            parent_turn.set_llm_input_messages(
                [{"role": "user", "content": "Compact the current investigation."}]
            )
            parent_turn.set_llm_output_messages(
                [{"role": "assistant", "content": "Compaction is needed.", "tool_calls": []}]
            )
        with (
            runtime.span("summarize", kind="AGENT"),
            runtime.span("LLMClient.invoke", kind="LLM") as child_turn,
        ):
            child_turn.set_llm_input_messages(
                [{"role": "system", "content": "Summarize the investigation."}]
            )
            child_turn.set_llm_output_messages(
                [{"role": "assistant", "content": "Summary", "tool_calls": []}]
            )

    parent, child = observer.snapshot()["events"]

    assert parent["agent"].get("parent_session_id") is None
    assert child["agent"]["parent_session_id"] == parent["agent"]["session_id"]
    assert child["parent_event_id"] is None


def test_tool_mediated_agent_keeps_the_tool_as_its_visible_parent() -> None:
    """Scenario: a Tool-started Agent preserves both the Tool hop and Agent ancestry."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span("main", kind="AGENT"):
        with runtime.span("LLMClient.invoke", kind="LLM") as parent_turn:
            parent_turn.set_llm_input_messages(
                [{"role": "user", "content": "Draft a parameter patch."}]
            )
            parent_turn.set_llm_output_messages(
                [{
                    "role": "assistant",
                    "content": "Starting patch work.",
                    "tool_calls": [{
                        "id": "call-patch",
                        "name": "patch.delegate",
                        "arguments": {},
                    }],
                }]
            )
        with runtime.span(
            "patch.delegate",
            kind="TOOL",
            input_value={"arguments": {}},
        ) as tool:
            with (
                runtime.span("patch", kind="AGENT"),
                runtime.span("LLMClient.invoke", kind="LLM") as child_turn,
            ):
                child_turn.set_llm_input_messages(
                    [{"role": "system", "content": "Propose a patch."}]
                )
                child_turn.set_llm_output_messages(
                    [{"role": "assistant", "content": "Patch", "tool_calls": []}]
                )
            tool.set_output({"tool_call_id": "call-patch", "status": "succeeded"})

    parent, tool_event, child = observer.snapshot()["events"]

    assert tool_event["parent_event_id"] == parent["event_id"]
    assert child["parent_event_id"] == tool_event["event_id"]
    assert child["agent"]["parent_session_id"] == parent["agent"]["session_id"]


def test_tool_request_and_parallel_executions_keep_their_own_inputs_and_outputs() -> None:
    """Scenario: Agent intent and two actual tool results remain independently visible."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span("main", kind="AGENT"):
        tool_calls = [
            {"id": "call-1", "name": "openapi.list_inputs", "arguments": {"path": "/a"}},
            {"id": "call-2", "name": "evidence.get", "arguments": {"case_id": "case-1"}},
        ]
        with runtime.span("LLMClient.invoke", kind="LLM") as turn:
            turn.set_llm_input_messages(
                [{"role": "system", "content": "Inspect"}, {"role": "user", "content": "Go"}]
            )
            turn.set_llm_output_messages(
                [{"role": "assistant", "content": None, "tool_calls": tool_calls}],
                summary={"finish_reason": "tool_calls"},
            )
        with runtime.span(
            "openapi.list_inputs",
            kind="TOOL",
            input_value={"arguments": {"path": "/a"}},
        ) as first:
            first.set_output(
                {"tool_call_id": "call-1", "name": "openapi.list_inputs", "status": "succeeded", "structured": {"inputs": ["query.q"]}}
            )
        with runtime.span(
            "evidence.get",
            kind="TOOL",
            input_value={"arguments": {"case_id": "TC1"}},
        ) as second:
            second.set_output(
                {"tool_call_id": "call-2", "name": "evidence.get", "status": "denied", "error": {"code": "not_found", "message": "Unknown case"}}
            )

    turn, first_tool, second_tool = observer.snapshot()["events"]

    assert turn["detail"]["output"]["tool_calls"] == tool_calls
    assert first_tool["parent_event_id"] == turn["event_id"]
    assert second_tool["parent_event_id"] == turn["event_id"]
    assert first_tool["detail"]["input"]["arguments"] == {"path": "/a"}
    assert first_tool["detail"]["output"]["structured"] == {"inputs": ["query.q"]}
    assert second_tool["detail"]["output"]["error"]["code"] == "not_found"
    assert second_tool["status"] == "warning"


def test_http_request_is_merged_into_one_tool_card() -> None:
    """Scenario: final target request and response enrich, rather than duplicate, a tool."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    with runtime.span("main", kind="AGENT"):
        with runtime.span("LLMClient.invoke", kind="LLM") as turn:
            turn.set_llm_input_messages(
                [{"role": "system", "content": "Inspect"}, {"role": "user", "content": "Probe"}]
            )
            turn.set_llm_output_messages(
                [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "probe-1",
                                "name": "restscope.http.request",
                                "arguments": {"method": "GET", "path": "/projects"},
                            }
                        ],
                    }
                ],
                summary={"finish_reason": "tool_calls"},
            )
        with runtime.live_span(
            "restscope.http.request",
            kind="TOOL",
            input_value={
                "arguments": {"method": "GET", "path": "/projects"},
            },
        ) as tool:
            exchange = observer.start_http_exchange(
                method="GET",
                path="/projects",
                url="https://api.test/projects?archived=true",
                headers={"Authorization": "Bearer visible-target-token"},
                request_kwargs={"json": {"filter": "active"}},
                operation_key="GET /projects",
                path_template="/projects",
            )
            assert exchange is not None
            exchange.finish(
                _Response(
                    status_code=400,
                    reason_phrase="Bad Request",
                    url="https://api.test/projects?archived=true",
                    headers={"content-type": "application/json"},
                    body=b'{"error":"invalid"}',
                )
            )
            tool.set_output(
                {
                    "tool_call_id": "probe-1",
                    "name": "restscope.http.request",
                    "status": "succeeded",
                    "structured": {"case_id": "TC9", "status_code": 400},
                }
            )

    events = observer.snapshot()["events"]
    tool_event = events[1]

    assert [event["kind"] for event in events] == ["agent_turn", "tool_call"]
    assert tool_event["detail"]["input"]["request"]["headers"]["Authorization"] == (
        "Bearer visible-target-token"
    )
    assert tool_event["detail"]["output"]["tool_result"]["structured"] == {
        "case_id": "TC9",
        "status_code": 400,
    }
    assert tool_event["detail"]["output"]["response"]["body"] == {
        "format": "json",
        "value": {"error": "invalid"},
    }


def test_http_exchange_outside_the_http_tool_is_not_a_timeline_card() -> None:
    """Scenario: nested target HTTP evidence has no independent card in v3."""
    from restscope.observability import LiveRunObserver

    observer = LiveRunObserver()
    observer.begin_run({})

    exchange = observer.start_http_exchange(
        method="GET",
        path="/health",
        url="https://api.test/health",
        headers={},
        request_kwargs=None,
        operation_key=None,
        path_template=None,
    )

    assert exchange is None
    assert observer.snapshot()["events"] == []


def test_new_run_replaces_prior_events_and_close_releases_snapshot() -> None:
    """Scenario: observer history never becomes cross-run persistence."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    first_run = observer.begin_run({"name": "first"})
    runtime = TracingRuntime(run_observer=observer)
    with runtime.span("RESTScopeApp.start", kind="CHAIN"):
        pass

    second_run = observer.begin_run({"name": "second"})
    snapshot = observer.snapshot()

    assert first_run != second_run
    assert snapshot["run"]["run_id"] == second_run
    assert snapshot["events"] == []
    observer.close()
    assert observer.snapshot()["run"] is None


def test_interrupted_run_marks_inflight_semantic_cards_as_stopped_warnings() -> None:
    """Scenario: caller interruption is not counted as a business failure."""
    import pytest

    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({"name": "interrupted"})
    runtime = TracingRuntime(run_observer=observer)

    with (
        pytest.raises(KeyboardInterrupt),
        runtime.span("main", kind="AGENT"),
        runtime.span("LLMClient.invoke", kind="LLM") as turn,
    ):
        turn.set_llm_input_messages(
            [{"role": "system", "content": "Wait"}, {"role": "user", "content": "Run"}]
        )
        raise KeyboardInterrupt

    cursor_before_stop = observer.snapshot()["latest_cursor"]
    observer.interrupt_run()
    stopped = observer.snapshot()
    stop_changes = observer.wait_after(cursor_before_stop, timeout_seconds=0)

    assert stopped["run"]["status"] == "stopped"
    assert stopped["run"]["ended_at"] is not None
    assert stopped["events"][0]["status"] == "warning"
    assert stopped["events"][0]["detail"]["stopped"] is True
    assert stop_changes[-1]["type"] == "run.update"

    observer.begin_run({"name": "replacement"})
    replacement = observer.snapshot()
    assert replacement["run"]["status"] == "running"
    assert replacement["events"] == []


def test_live_only_hidden_phase_does_not_add_a_phoenix_or_timeline_span() -> None:
    """Scenario: an old UI-only phase is invisible and never reaches Phoenix."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    class Backend:
        """Record every requested exported span name."""

        def __init__(self) -> None:
            self.names: list[str] = []

        def start_as_current_span(self, name: str):
            """Fail the test if a UI-only span reaches the backend."""
            self.names.append(name)
            raise AssertionError("live-only phase reached tracing backend")

        def close(self) -> None:
            """Satisfy the tracing backend lifecycle contract."""

    observer = LiveRunObserver()
    observer.begin_run({})
    backend = Backend()
    runtime = TracingRuntime(backend=backend, run_observer=observer)

    with runtime.live_span(
        "cleanup",
        kind="CHAIN",
        attributes={"restscope.operation.round": 2},
    ):
        pass

    assert observer.snapshot()["events"] == []
    assert backend.names == []


def test_target_api_client_keeps_binary_response_evidence_inside_the_tool() -> None:
    """Scenario: final headers/query and truncated binary bytes enrich one HTTP tool."""
    import httpx

    from restscope.observability import LiveRunObserver, TracingRuntime
    from restscope.target_api import TargetAPIClient, prepare_target_request

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer target-token"
        assert request.headers["cookie"] == "session=visible"
        return httpx.Response(
            500,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01\x02\x03",
            request=request,
        )

    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
        run_observer=observer,
    )
    prepared = prepare_target_request(
        method="POST",
        base_url="https://api.test",
        path="/projects",
        query_items=[("tag", "one"), ("tag", "two")],
        context_headers={
            "Authorization": "Bearer target-token",
            "Cookie": "session=visible",
        },
    )

    with runtime.live_span(
        "restscope.http.request",
        kind="TOOL",
        input_value={"arguments": {"method": "POST", "path": "/projects"}},
    ) as tool:
        response = client.send(
            prepared,
            request_kwargs={"json": {"name": "demo"}},
            failure_body_limit=2,
            truncate_body=True,
        )
        tool.set_output(
            {"name": "restscope.http.request", "status": "succeeded"}
        )

    event = observer.snapshot()["events"][0]

    assert response.body == b"\x00\x01"
    assert response.body_truncated is True
    assert event["kind"] == "tool_call"
    assert event["detail"]["input"]["request"]["query"] == [
        {"name": "tag", "value": "one"},
        {"name": "tag", "value": "two"},
    ]
    assert event["detail"]["input"]["request"]["headers"]["Authorization"] == (
        "Bearer target-token"
    )
    # Live Observer owns a separate one-MiB view; the caller's two-byte
    # diagnostic limit cannot silently reduce human observability evidence.
    assert event["detail"]["output"]["response"]["body"] == {
        "format": "base64",
        "value": "AAECAw==",
    }
    assert event["detail"]["output"]["response"]["body_truncated"] is False


def test_target_api_client_marks_timeout_without_replacing_the_public_error() -> None:
    """Scenario: timeout evidence is visible while callers receive the same timeout."""
    import httpx
    import pytest

    from restscope.observability import LiveRunObserver, TracingRuntime
    from restscope.target_api import (
        TargetAPIClient,
        TargetAPITimeout,
        prepare_target_request,
    )

    observer = LiveRunObserver()
    observer.begin_run({})
    runtime = TracingRuntime(run_observer=observer)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider detail", request=request)

    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
        run_observer=observer,
    )
    prepared = prepare_target_request(
        method="GET",
        base_url="https://api.test",
        path="/slow",
    )

    with runtime.live_span(
        "restscope.http.request",
        kind="TOOL",
        input_value={"arguments": {"method": "GET", "path": "/slow"}},
    ), pytest.raises(TargetAPITimeout, match="timed out"):
        client.send(prepared, success_body_limit=1024)

    event = observer.snapshot()["events"][0]
    assert event["status"] == "failed"
    assert event["detail"]["output"]["transport_error"]["type"] == (
        "TargetAPITimeout"
    )
