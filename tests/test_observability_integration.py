"""Regression scenarios for observability integration. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")


def _recording_runtime(*, secret_values=()):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from restscope.observability import Redactor
    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        redactor=Redactor(secret_values),
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )
    return runtime, exporter


def test_llm_client_records_sanitized_request_response_and_metrics() -> None:
    """Scenario: verify that llm client records sanitized request response and metrics."""
    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMProviderRegistry,
        LLMReasoningConfig,
        LLMRequest,
        LLMResponse,
        ToolCall,
        ToolSpec,
    )
    from restscope.llm.providers.base import BaseLLMProvider

    reasoning = "private reasoning body"

    class StubProvider(BaseLLMProvider):
        name = "stub"

        def invoke(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model=request.model,
                content="done",
                reasoning_content=reasoning,
                parsed_json={"ok": True},
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="catalog.lookup",
                        arguments={"query": "request-secret"},
                        provider_context={"reasoning_content": reasoning},
                    )
                ],
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                finish_reason="tool_calls",
                latency_ms=25,
                metadata={"strict_tool_beta": True},
            )

    registry = LLMProviderRegistry()
    registry.register(StubProvider())
    runtime, exporter = _recording_runtime(secret_values=["request-secret"])
    client = LLMClient(registry, tracing_runtime=runtime)

    response = client.invoke(
        LLMRequest(
            provider="stub",
            model="stub-model",
            messages=[LLMMessage(role="user", content="request-secret")],
            temperature=0.25,
            max_tokens=321,
            response_format="json",
            reasoning=LLMReasoningConfig(mode="enabled", effort="high"),
            tools=[
                ToolSpec(
                    name="catalog.lookup",
                    description="Lookup a catalog entry",
                    kind="local_function",
                    input_schema={"type": "object"},
                    strict=True,
                )
            ],
            tool_choice="auto",
        )
    )
    runtime.close()

    assert response.total_tokens == 18
    span = exporter.get_finished_spans()[0]
    rendered = json.dumps(
        {
            "attributes": dict(span.attributes),
            "events": [dict(event.attributes) for event in span.events],
        },
        default=str,
    )
    output = json.loads(span.attributes["output.value"])
    input_value = json.loads(span.attributes["input.value"])

    assert span.name == "LLMClient.invoke"
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["llm.provider"] == "stub"
    assert span.attributes["llm.model_name"] == "stub-model"
    assert span.attributes["llm.temperature"] == 0.25
    assert span.attributes["llm.max_tokens"] == 321
    assert span.attributes["llm.response_format"] == "json"
    assert span.attributes["llm.reasoning.mode"] == "enabled"
    assert span.attributes["llm.reasoning.effort"] == "high"
    assert json.loads(span.attributes["llm.tool_names"]) == ["catalog.lookup"]
    assert span.attributes["llm.tool_strict"] is True
    assert span.attributes["llm.tool_choice"] == "auto"
    assert span.attributes["llm.token_count.total"] == 18
    assert span.attributes["restscope.llm.latency_ms"] == 25
    assert span.attributes["restscope.llm.strict_tool_beta"] is True
    assert input_value == {"message_count": 1, "roles": ["user"]}
    assert span.attributes["llm.input_messages.0.message.role"] == "user"
    assert span.attributes["llm.input_messages.0.message.content"] == "***REDACTED***"
    assert set(output) == {
        "parsed_json",
        "tool_calls",
        "finish_reason",
    }
    assert output["finish_reason"] == "tool_calls"
    assert output["parsed_json"] == {"ok": True}
    assert output["tool_calls"] == [{"id": "call-1", "name": "catalog.lookup"}]
    assert span.attributes["llm.finish_reason"] == "tool_calls"
    assert span.attributes["llm.output_messages.0.message.role"] == "assistant"
    assert span.attributes["llm.output_messages.0.message.content"] == "done"
    assert (
        span.attributes["llm.output_messages.0.message.tool_calls.0.tool_call.id"]
        == "call-1"
    )
    assert (
        span.attributes[
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.name"
        ]
        == "catalog.lookup"
    )
    assert json.loads(
        span.attributes[
            "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments"
        ]
    ) == {"query": "***REDACTED***"}
    assert "request-secret" not in rendered
    assert reasoning not in rendered


def test_llm_provider_unavailable_span_records_only_stable_failure_facts() -> None:
    """Capacity traces omit the provider body retained on the internal cause."""
    from opentelemetry.trace.status import StatusCode

    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMProviderRegistry,
        LLMRequest,
        ProviderUnavailableError,
    )
    from restscope.llm.providers.base import BaseLLMProvider

    provider_body = "unregistered private provider response body"
    original = RuntimeError(provider_body)
    unavailable = ProviderUnavailableError(status_code=503, retry_limit=3)
    unavailable.__cause__ = original

    class UnavailableProvider(BaseLLMProvider):
        name = "unavailable"

        def invoke(self, request: LLMRequest):
            del request
            raise unavailable

    registry = LLMProviderRegistry()
    registry.register(UnavailableProvider())
    runtime, exporter = _recording_runtime()

    with pytest.raises(ProviderUnavailableError):
        LLMClient(registry, tracing_runtime=runtime).invoke(
            LLMRequest(
                provider="unavailable",
                model="only-model",
                messages=[LLMMessage(role="user", content="bounded")],
            )
        )
    runtime.close()

    span = exporter.get_finished_spans()[0]
    rendered = json.dumps(
        {
            "attributes": dict(span.attributes),
            "events": [dict(event.attributes) for event in span.events],
        },
        default=str,
    )
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["restscope.llm.error_code"] == "provider_unavailable"
    assert span.attributes["http.response.status_code"] == 503
    assert span.attributes["restscope.llm.provider_retry_limit"] == 3
    assert "restscope.llm.provider_retry_count" not in span.attributes
    assert provider_body not in rendered


def test_agent_toolbox_uses_actual_tool_name_and_sanitizes_trace_payload() -> None:
    """The Agent-owned execution boundary emits safe tool spans."""
    from opentelemetry.trace.status import StatusCode

    from restscope.llm import ToolCall, ToolSpec
    from restscope.openapi_parser import OpenAPIParser
    from restscope.tools import AgentToolbox
    from restscope.tools.context import ToolContext

    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Tracing", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    runtime, exporter = _recording_runtime(secret_values=["tool-secret"])
    context = ToolContext(
        ir=OpenAPIParser.parse(json.dumps(schema)),
        baseline_schema_source={"kind": "inline", "content": json.dumps(schema)},
        base_url="http://example.test",
        headers={},
    )
    toolbox = AgentToolbox(tracing_runtime=runtime)
    toolbox.register(
        spec=ToolSpec(
            name="test.echo",
            description="Echo safely",
            kind="local_function",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        execute=lambda **arguments: {
            "structured": {
                "operation_count": len(context.ir.operations),
                "arguments": arguments,
            }
        },
    )

    def fail_handler(**arguments):
        del arguments
        raise RuntimeError("tool failed with tool-secret")

    toolbox.register(
        spec=ToolSpec(
            name="test.fail",
            description="Fail safely",
            kind="local_function",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        ),
        execute=fail_handler,
    )

    result = toolbox.execute(
        ToolCall(
            id="call-tool",
            name="test.echo",
            arguments={
                "token": "tool-secret",
                "password": "visible-generated-password",
            },
        )
    )
    failed_result = toolbox.execute(
        ToolCall(id="call-fail", name="test.fail", arguments={})
    )
    runtime.close()

    assert result.status == "succeeded"
    assert failed_result.status == "failed"
    assert toolbox.tracing_runtime is runtime
    spans = {span.name: span for span in exporter.get_finished_spans()}
    span = spans["test.echo"]
    rendered = json.dumps(dict(span.attributes), default=str)

    assert span.name == "test.echo"
    assert span.attributes["openinference.span.kind"] == "TOOL"
    assert span.attributes["tool.name"] == "test.echo"
    assert span.attributes["restscope.tool.status"] == "succeeded"
    assert "tool-secret" not in rendered
    assert result.structured["arguments"] == {
        "token": "***REDACTED***",
        "password": "visible-generated-password",
    }
    assert "visible-generated-password" in rendered
    assert spans["test.fail"].status.status_code is StatusCode.ERROR
    error_event = spans["test.fail"].events[0]
    assert "RuntimeError" in error_event.attributes["exception.stacktrace"]
    assert "tool-secret" not in error_event.attributes["exception.stacktrace"]


def test_agent_toolbox_propagates_provider_unavailable_without_tracing_cause() -> None:
    """A nested model outage keeps stable Tool span facts and a private cause."""
    from opentelemetry.trace.status import StatusCode

    from restscope.llm import (
        ProviderUnavailableError,
        ToolCall,
        ToolSpec,
    )
    from restscope.tools import AgentToolbox

    provider_body = "unregistered private nested provider response body"
    unavailable = ProviderUnavailableError(status_code=503, retry_limit=3)
    unavailable.__cause__ = RuntimeError(provider_body)

    def fail() -> dict:
        """Expose the nested provider failure through one registered tool."""
        raise unavailable

    runtime, exporter = _recording_runtime()
    toolbox = AgentToolbox(tracing_runtime=runtime)
    toolbox.register(
        spec=ToolSpec(
            name="patch.review",
            description="Review one generated Patch.",
            kind="local_function",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
        ),
        execute=fail,
    )

    with pytest.raises(ProviderUnavailableError) as caught:
        toolbox.execute(ToolCall(id="review", name="patch.review", arguments={}))
    runtime.close()

    span = exporter.get_finished_spans()[0]
    rendered = json.dumps(
        {
            "attributes": dict(span.attributes),
            "events": [dict(event.attributes) for event in span.events],
        },
        default=str,
    )
    assert caught.value is unavailable
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["restscope.llm.error_code"] == "provider_unavailable"
    assert span.attributes["http.response.status_code"] == 503
    assert span.attributes["restscope.llm.provider_retry_limit"] == 3
    assert "restscope.llm.provider_retry_count" not in span.attributes
    assert provider_body not in rendered


def test_parallel_agent_tools_keep_the_current_trace_parent() -> None:
    """Scenario: concurrent tool spans remain children of the calling Agent."""
    import threading

    from restscope.llm import ToolCall, ToolSpec
    from restscope.tools import AgentToolbox

    # Requiring both implementations to arrive before either returns proves
    # that this scenario crosses the real worker-thread boundary.
    barrier = threading.Barrier(2, timeout=1)

    def query(*, value: str) -> dict:
        """Wait for the sibling call and return one schema-valid value."""
        barrier.wait()
        return {"structured": {"value": value}}

    runtime, exporter = _recording_runtime()
    toolbox = AgentToolbox(tracing_runtime=runtime)
    toolbox.register(
        spec=ToolSpec(
            name="catalog.query",
            description="Read one catalog value.",
            kind="local_function",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        execute=query,
    )

    with runtime.span("main", kind="AGENT"):
        results = toolbox.execute_many(
            [
                ToolCall(
                    id="first-query",
                    name="catalog.query",
                    arguments={"value": "first"},
                ),
                ToolCall(
                    id="second-query",
                    name="catalog.query",
                    arguments={"value": "second"},
                ),
            ]
        )
    runtime.close()

    spans = exporter.get_finished_spans()
    agent_span = next(span for span in spans if span.name == "main")
    tool_spans = [span for span in spans if span.name == "catalog.query"]

    assert [result.status for result in results] == ["succeeded", "succeeded"]
    assert len(tool_spans) == 2
    assert all(
        span.context.trace_id == agent_span.context.trace_id for span in tool_spans
    )
    assert all(
        span.parent is not None and span.parent.span_id == agent_span.context.span_id
        for span in tool_spans
    )


def test_app_owns_one_runtime_and_emits_chain_hierarchy(
    monkeypatch, tmp_path: Path
) -> None:
    """The blocking App and fresh Orchestrator roots form one trace hierarchy."""
    from restscope import RESTScopeApp
    from restscope.agent import AgentProfile, SystemAgentTask
    from restscope.config import RESTScopeConfig
    from restscope.harness import AgentRuntimeDefinition, SystemAgentDefinition
    from restscope.llm import LLMClient, LLMModelConfig, LLMResponse
    from restscope.llm.registry import LLMProviderRegistry
    from restscope.orchestration.contracts import (
        orchestrator_output_schema,
        validate_orchestrator_output,
    )
    from restscope.orchestration.models import OrchestratorDecision

    class Provider:
        """Return a minimal local replan and completion for tracing."""

        name = "scripted"

        def __init__(self) -> None:
            """Keep the two decisions required by a valid Orchestration run."""
            self.outputs = [
                {
                    "kind": "replan",
                    "expected_plan_revision": 0,
                    "reason": "Create one bounded tracing milestone.",
                    "milestones": [
                        {
                            "title": "Trace startup",
                            "purpose": "Exercise the App-owned Orchestrator lifecycle.",
                            "success_criteria": ["The tracing chain is recorded."],
                        }
                    ],
                },
                {
                    "kind": "complete",
                    "expected_plan_revision": 1,
                    "goal_criteria": [
                        {
                            "criterion_id": f"goal_{index}",
                            "status": "unknown",
                            "explanation": "This test checks tracing, not API evidence.",
                        }
                        for index in range(1, 4)
                    ],
                    "summary": "Tracing lifecycle completed.",
                    "unresolved": ["API evidence is outside this tracing scenario."],
                },
            ]

        def invoke(self, request):
            """Return the next decision while the real client records its span."""
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json=self.outputs.pop(0),
            )

    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Tracing App", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.openai_compatible]\n"
        'api_key_env = "MODEL_API_KEY"\n'
        "\n"
        "[models.default]\n"
        'provider = "openai_compatible"\n'
        'model = "unused-model"\n',
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    database = tmp_path / "tracing-app.sqlite"
    env_file.write_text(
        "\n".join(
            [
                f"MODELS_FILE={models_file}",
                "MODEL_API_KEY=model-secret",
                f"DB_URL=sqlite:///{database}",
            ]
        ),
        encoding="utf-8",
    )
    runtime, exporter = _recording_runtime()
    registry = LLMProviderRegistry()
    registry.register(Provider())
    agent_definition = AgentRuntimeDefinition(
        profiles=(
            AgentProfile(
                name="orchestrator",
                model_config_name="thinking",
                reasoning_effort="none",
            ),
        ),
        models=(
            LLMModelConfig(
                name="thinking",
                provider="scripted",
                model="thinking-model",
                max_tokens=512,
                context_window_tokens=8_192,
            ),
        ),
        client=LLMClient(registry, tracing_runtime=runtime),
        system_agents=(
            SystemAgentDefinition(
                profile_name="orchestrator",
                adapt_task=SystemAgentTask.model_validate,
                output_model=OrchestratorDecision,
                build_output_schema=orchestrator_output_schema,
                validate_output=validate_orchestrator_output,
                output_schema_name="OrchestratorDecision",
            ),
        ),
    )
    monkeypatch.setattr(
        "restscope.app.composition._build_app_tracing_runtime",
        lambda _config: runtime,
    )
    monkeypatch.setattr(
        "restscope.app.composition._build_agent_runtime_definition",
        lambda *_args, **_kwargs: agent_definition,
    )
    app = RESTScopeApp(RESTScopeConfig.from_environment(env_file))
    app.initialize(
        schema_source={"kind": "inline", "content": json.dumps(schema)},
        base_url="http://example.test",
        headers={"Authorization": "Bearer header-secret"},
    )

    assert app.start() is None
    with runtime.span(
        "test.config-secrets",
        kind="CHAIN",
        input_value={
            "keys": ["model-secret"],
            "authorization": "Bearer header-secret",
        },
    ):
        pass
    app.close()

    finished_spans = exporter.get_finished_spans()
    spans = {span.name: span for span in finished_spans}
    app_span = spans["RESTScopeApp.start"]
    agent_spans = [span for span in finished_spans if span.name == "Agent.run"]
    model_spans = [span for span in finished_spans if span.name == "LLMClient.invoke"]
    rendered = json.dumps(
        [dict(span.attributes) for span in finished_spans],
        default=str,
    )

    assert len(agent_spans) == 2
    assert len(model_spans) == 2
    assert all(
        span.parent is not None and span.parent.span_id == app_span.context.span_id
        for span in agent_spans
    )
    agent_span_ids = {span.context.span_id for span in agent_spans}
    assert all(
        span.parent is not None and span.parent.span_id in agent_span_ids
        for span in model_spans
    )
    assert app_span.attributes["openinference.span.kind"] == "CHAIN"
    assert all(
        span.attributes["openinference.span.kind"] == "CHAIN" for span in agent_spans
    )
    assert all(
        span.attributes["openinference.span.kind"] == "LLM" for span in model_spans
    )
    assert json.loads(app_span.attributes["output.value"]) == {
        "runtime": "orchestration",
        "status": "completed",
    }
    assert app_span.attributes["restscope.output.truncated"] is False
    assert "header-secret" in rendered
    assert "model-secret" not in rendered


def test_harness_rebinds_only_its_owned_trace_consumers() -> None:
    """Harness tracing replacement stays at the Harness ownership seam."""
    from restscope.harness import build_harness
    from restscope.observability import Redactor, TracingRuntime

    old_runtime = TracingRuntime.disabled(redactor=Redactor(["old-key"]))
    app_runtime = TracingRuntime.disabled(redactor=Redactor(["app-key"]))
    capabilities = build_harness(
        tracing_runtime=old_runtime,
        sources={
            "example": {
                "kind": "mcp",
                "tools": [
                    {
                        "name": "inspect",
                        "description": "Inspect",
                        "inputSchema": {"type": "object"},
                    }
                ],
                "call_tool": lambda _name, _arguments: {"content": "ok"},
            }
        },
    )
    capabilities.bind_tracing_runtime(app_runtime)

    assert capabilities.external_tools is not None
    assert capabilities.external_tools.tracing_runtime is app_runtime
    assert not hasattr(capabilities, "operation_testing_service")

    if capabilities.mcp_host is not None:
        capabilities.mcp_host.close()
    app_runtime.close()
    old_runtime.close()


def test_http_request_tool_keeps_full_result_while_trace_output_is_bounded() -> None:
    """Scenario: verify that http request tool keeps full result while trace output is bounded."""
    import httpx

    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser
    from restscope.tools import AgentToolbox
    from restscope.tools.context import ToolContext
    from restscope.tools.http import (
        TargetHTTPRequestTool,
        http_request_tool_spec,
    )

    response_body = f"{'x' * 70000} Bearer runtime-secret"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            text=response_body,
        )
    )
    runtime, exporter = _recording_runtime(secret_values=["Bearer runtime-secret"])
    http_tool = TargetHTTPRequestTool(
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    context = ToolContext(
        ir=OpenAPIParser.parse(
            {
                "openapi": "3.0.3",
                "info": {"title": "Trace HTTP", "version": "1"},
                "paths": {
                    "/large": {"get": {"responses": {"200": {"description": "ok"}}}}
                },
            }
        ),
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={"Authorization": "Bearer runtime-secret"},
    )
    toolbox = AgentToolbox(tracing_runtime=runtime)
    toolbox.register(
        spec=http_request_tool_spec(),
        execute=lambda **arguments: http_tool.execute(context, **arguments),
    )

    result = toolbox.execute(
        ToolCall(
            id="trace-http",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/large"},
        )
    )
    runtime.close()

    assert result.status == "succeeded"
    assert len(result.structured["body"]) > 65536
    assert "runtime-secret" not in result.model_dump_json()
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "restscope.http.request"
    )
    assert span.attributes["restscope.output.truncated"] is True
    assert span.attributes["restscope.output.original_size_bytes"] > 65536
    assert "runtime-secret" not in span.attributes["output.value"]


def test_generic_batch_emits_sanitized_batch_and_case_spans(
    tmp_path: Path,
    api_behavior_catalog,
) -> None:
    """The generic Batch runner traces structure without target secrets."""
    import httpx

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
    )
    from restscope.request_generation.parameter_patch import SemanticParameterPatch
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext

    class UnreadableBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("testing traces must not consume response bodies")

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Generated trace", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "token",
                                "in": "query",
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /search"]
    catalog = RequestGenerationConfigStore()
    assert catalog.initialize_once(ir) is True
    patch_runtime = RequestGenerationPatchRuntime(store=catalog, ir_provider=lambda: ir)
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.token",
                    "inclusion_probability": 1,
                    "strategy": {"type": "constant", "value": "generated-secret"},
                }
            ]
        }
    )
    validated = patch_runtime.validate(
        operation_key=operation.operation_key,
        expected_revision=0,
        affected_inputs=("query.token",),
        patch=patch,
    )
    patch_runtime.apply(
        operation_key=operation.operation_key,
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("query.token",),
        patch=patch,
    )
    runtime, exporter = _recording_runtime(secret_values=["llm-api-key"])
    service = OperationTestingService(
        config_store=catalog,
        api_behavior_catalog=api_behavior_catalog,
        tracing_runtime=runtime,
        target_api_client=TargetAPIClient(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"Content-Type": "text/plain"},
                        stream=UnreadableBody(),
                    )
                ),
                **kwargs,
            )
        ),
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={
            "kind": "inline",
            "format": "json",
            "content": "{}",
        },
        base_url="https://api.example.test",
        headers={
            "Authorization": "Bearer runtime-secret",
            "X-CSRF-Token": "runtime-csrf-secret",
            "X-Configured-Key": "llm-api-key",
        },
    )

    result = service.run_batch(
        context,
        operation_key=operation.operation_key,
        test_mode="happy_path",
        case_count=2,
        seed=4,
    )
    runtime.close()

    rendered_result = json.dumps(
        {
            "operation_key": result.operation_key,
            "seed": result.seed,
            "cases": [case.model_dump(mode="json") for case in result.cases],
        }
    )
    assert "runtime-secret" not in rendered_result
    assert "runtime-csrf-secret" not in rendered_result
    assert "Authorization" not in rendered_result
    assert "X-CSRF-Token" not in rendered_result
    assert "generated-secret" in rendered_result
    spans = list(exporter.get_finished_spans())
    batch = next(
        span for span in spans if span.name == "OperationTestingService.run_batch"
    )
    cases = [span for span in spans if span.name == "RESTScopeTestCase.execute"]
    assert len(cases) == 2
    assert all(span.parent.span_id == batch.context.span_id for span in cases)
    rendered_spans = json.dumps([dict(span.attributes) for span in spans], default=str)
    assert "runtime-secret" not in rendered_spans
    assert "runtime-csrf-secret" not in rendered_spans
    assert "generated-secret" not in rendered_spans
    assert "llm-api-key" not in rendered_result
    assert "llm-api-key" not in rendered_spans
