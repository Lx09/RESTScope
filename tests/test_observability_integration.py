from __future__ import annotations

import json
from pathlib import Path

import pytest


pytest.importorskip("opentelemetry.sdk")


def _recording_runtime(*, secret_values=()):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime
    from restscope.observability.sanitizer import TraceSanitizer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        sanitizer=TraceSanitizer(secret_values=secret_values),
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )
    return runtime, exporter


def test_llm_client_records_sanitized_request_response_and_metrics() -> None:
    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
        ToolCall,
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
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="openapi.inspect",
                        provider_context={"reasoning_content": reasoning},
                    )
                ],
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
                latency_ms=25,
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

    assert span.name == "LLMClient.invoke"
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["llm.provider"] == "stub"
    assert span.attributes["llm.model_name"] == "stub-model"
    assert span.attributes["llm.token_count.total"] == 18
    assert span.attributes["restscope.llm.latency_ms"] == 25
    assert "request-secret" not in rendered
    assert reasoning not in rendered
    assert output["tool_calls"][0]["provider_context"]["reasoning_content_present"] is True
    assert output["tool_calls"][0]["provider_context"]["reasoning_content_length"] == len(reasoning)


def test_tool_executor_uses_actual_tool_name_and_sanitizes_trace_payload() -> None:
    from opentelemetry.trace.status import StatusCode

    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.llm import ToolCall, ToolSpec
    from restscope.openapi_parser import OpenAPIParser

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
    capabilities = build_capabilities(presets=(), tracing_runtime=runtime)
    capabilities.tool_registry.register(
        spec=ToolSpec(
            name="test.echo",
            description="Echo safely",
            kind="local_function",
            input_schema={"type": "object"},
        ),
        handler=lambda context, **arguments: {
            "structured": {
                "operation_count": len(context.ir.operations),
                "argument_keys": sorted(arguments),
            }
        },
    )

    def fail_handler(context, **arguments):
        del context, arguments
        raise RuntimeError("tool failed")

    capabilities.tool_registry.register(
        spec=ToolSpec(
            name="test.fail",
            description="Fail safely",
            kind="local_function",
            input_schema={"type": "object"},
        ),
        handler=fail_handler,
    )
    capabilities.tool_executor.bind_context(
        ToolContext(
            ir=OpenAPIParser.parse(json.dumps(schema)),
            baseline_schema_source={"kind": "inline", "content": json.dumps(schema)},
            base_url="http://example.test",
            headers={},
        )
    )

    result = capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="call-tool",
            name="test.echo",
            arguments={"token": "tool-secret"},
        ),
        role="decision_maker",
        state={},
    )
    failed_result = capabilities.tool_executor.execute(
        tool_call=ToolCall(id="call-fail", name="test.fail", arguments={}),
        role="decision_maker",
        state={},
    )
    runtime.close()

    assert result.status == "succeeded"
    assert failed_result.status == "failed"
    assert capabilities.tool_executor.tracing_runtime is runtime
    spans = {span.name: span for span in exporter.get_finished_spans()}
    span = spans["test.echo"]
    rendered = json.dumps(dict(span.attributes), default=str)

    assert span.name == "test.echo"
    assert span.attributes["openinference.span.kind"] == "TOOL"
    assert span.attributes["tool.name"] == "test.echo"
    assert span.attributes["restscope.tool.status"] == "succeeded"
    assert "tool-secret" not in rendered
    assert spans["test.fail"].status.status_code is StatusCode.ERROR


def test_app_owns_one_runtime_and_emits_chain_agent_hierarchy(tmp_path: Path) -> None:
    from restscope import RESTScopeApp
    from restscope.agent import (
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        RESTScopeRunRequest,
    )
    from restscope.restscope_config import RESTScopeConfig

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
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "THINK_API_KEY=thinking-secret",
                "FAST_API_KEY=fast-secret",
            ]
        ),
        encoding="utf-8",
    )
    runtime, exporter = _recording_runtime()
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_runner=FakeOperationTestRunner(),
        dependency_analyzer=FakeOperationDependencyAnalyzer(),
        tracing_runtime=runtime,
    )
    context = app.initialize(
        schema_source={"kind": "inline", "content": json.dumps(schema)},
        base_url="http://example.test",
        headers={"Authorization": "Bearer header-secret"},
    )

    report = app.run(
        RESTScopeRunRequest(
            allow_live_testing=True,
            metadata={"task_id": "trace-task"},
        )
    )
    with runtime.span(
        "test.config-secrets",
        kind="CHAIN",
        input_value={"keys": ["thinking-secret", "fast-secret"]},
    ):
        pass
    app.close()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    app_span = spans["RESTScopeApp.run"]
    graph_span = spans["RESTScopeMainGraph.run"]
    operation_span = spans["OperationTestAgent.run"]
    rendered = json.dumps(
        [dict(span.attributes) for span in spans.values()],
        default=str,
    )

    assert app.tracing_runtime is runtime
    assert app.capability_runtime.tool_executor.tracing_runtime is runtime
    with pytest.raises(AttributeError):
        app.tracing_runtime = runtime
    assert graph_span.parent.span_id == app_span.context.span_id
    assert operation_span.parent.span_id == graph_span.context.span_id
    assert app_span.attributes["openinference.span.kind"] == "CHAIN"
    assert graph_span.attributes["openinference.span.kind"] == "AGENT"
    assert operation_span.attributes["openinference.span.kind"] == "AGENT"
    assert app_span.attributes["restscope.task_id"] == "trace-task"
    assert "header-secret" not in rendered
    assert "thinking-secret" not in rendered
    assert "fast-secret" not in rendered
    assert "tracing" not in report.model_dump(mode="json")
    assert not hasattr(context, "tracing_runtime")


def test_openapi_retrieval_emits_tool_agent_llm_and_internal_tool_spans() -> None:
    from restscope.agent.openapi_retrieval import (
        OpenAPIRetrievalAgent,
        OpenAPIRetrievalRequest,
        ParameterValueProducerQuery,
        register_openapi_retrieval_tool,
    )
    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMModelConfig,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
        ToolCall,
    )
    from restscope.llm.providers.base import BaseLLMProvider
    from restscope.openapi_parser import OpenAPIParser

    schema = {
        "openapi": "3.0.0",
        "info": {"title": "Retrieval Trace", "version": "1"},
        "paths": {
            "/users": {
                "post": {
                    "operationId": "createUser",
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"userId": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/users/{userId}": {
                "get": {
                    "operationId": "getUser",
                    "parameters": [
                        {
                            "name": "userId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }

    class RetrievalProvider(BaseLLMProvider):
        name = "stub"

        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, request: LLMRequest) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    provider=self.name,
                    model=request.model,
                    tool_calls=[
                        ToolCall(
                            id="search",
                            name="openapi.search_symbols",
                            arguments={
                                "query": "userId",
                                "scopes": ["response_field"],
                                "limit": 5,
                            },
                        )
                    ],
                )
            match = json.loads(request.messages[-1].content)["results"][0]
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json={
                    "status": "found",
                    "candidates": [
                        {
                            "operation": match["operation"],
                            "confidence": "high",
                            "value_locations": [match["location"]],
                            "rationale": "The response contains userId.",
                            "evidence_refs": [match["evidence_id"]],
                        }
                    ],
                    "conflicts": [],
                    "evidence_sufficient": True,
                    "limitations": [],
                    "warnings": [],
                },
            )

    runtime, exporter = _recording_runtime()
    registry = LLMProviderRegistry()
    registry.register(RetrievalProvider())
    agent = OpenAPIRetrievalAgent(
        client=LLMClient(registry, tracing_runtime=runtime),
        model=LLMModelConfig(
            role="openapi_retrieval",
            provider="stub",
            model="stub-model",
            tool_choice="auto",
        ),
        tracing_runtime=runtime,
    )
    capabilities = build_capabilities(presets=(), tracing_runtime=runtime)
    spec = register_openapi_retrieval_tool(capabilities.tool_registry, agent)
    serialized_schema = json.dumps(schema)
    capabilities.tool_executor.bind_context(
        ToolContext(
            ir=OpenAPIParser.parse(serialized_schema),
            baseline_schema_source={"kind": "inline", "content": serialized_schema},
            base_url=None,
            headers={},
        )
    )
    request = OpenAPIRetrievalRequest(
        query=ParameterValueProducerQuery(
            objective="parameter_value_producer",
            consumer_method="GET",
            consumer_path="/users/{userId}",
            parameter_name="userId",
        )
    )

    result = capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="retrieve",
            name=spec.name,
            arguments=request.model_dump(mode="json"),
        ),
        role="decision_maker",
        state={},
    )
    runtime.close()

    assert result.status == "succeeded"
    spans = list(exporter.get_finished_spans())
    by_name = {span.name: span for span in spans}
    wrapper = by_name["restscope.openapi.retrieve"]
    agent_span = by_name["OpenAPIRetrievalAgent.retrieve"]
    internal_tool = by_name["openapi.search_symbols"]
    llm_spans = [span for span in spans if span.name == "LLMClient.invoke"]

    assert agent_span.parent.span_id == wrapper.context.span_id
    assert internal_tool.parent.span_id == agent_span.context.span_id
    assert len(llm_spans) == 2
    assert all(span.parent.span_id == agent_span.context.span_id for span in llm_spans)
    assert wrapper.attributes["openinference.span.kind"] == "TOOL"
    assert agent_span.attributes["openinference.span.kind"] == "AGENT"
    assert internal_tool.attributes["openinference.span.kind"] == "TOOL"


def test_http_request_tool_keeps_full_result_while_trace_output_is_bounded() -> None:
    import httpx

    from restscope.capabilities import (
        ToolContext,
        build_capabilities,
        register_http_request_tool,
    )
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser

    response_body = f"{'x' * 70000} Bearer runtime-secret"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            text=response_body,
        )
    )
    runtime, exporter = _recording_runtime(secret_values=["Bearer runtime-secret"])
    capabilities = build_capabilities(presets=(), tracing_runtime=runtime)
    register_http_request_tool(
        capabilities.tool_registry,
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    capabilities.tool_executor.bind_context(
        ToolContext(
            ir=OpenAPIParser.parse(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "Trace HTTP", "version": "1"},
                    "paths": {"/large": {"get": {"responses": {"200": {"description": "ok"}}}}},
                }
            ),
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={"Authorization": "Bearer runtime-secret"},
        )
    )

    result = capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="trace-http",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/large"},
        ),
        role="future_agent",
        state={},
    )
    runtime.close()

    assert result.status == "succeeded"
    assert len(result.structured["body"]) > 65536
    assert "runtime-secret" not in result.model_dump_json()
    span = next(span for span in exporter.get_finished_spans() if span.name == "restscope.http.request")
    assert span.attributes["restscope.output.truncated"] is True
    assert span.attributes["restscope.output.original_size_bytes"] > 65536
    assert "runtime-secret" not in span.attributes["output.value"]
