"""Regression scenarios for observability integration. Each test documents one observable contract or failure boundary."""

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
    from restscope.redaction import Redactor

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
    assert span.attributes["llm.tool_choice"] == "auto"
    assert span.attributes["llm.token_count.total"] == 18
    assert span.attributes["restscope.llm.latency_ms"] == 25
    assert input_value == {"message_count": 1, "roles": ["user"]}
    assert (
        span.attributes["llm.input_messages.0.message.role"]
        == "user"
    )
    assert (
        span.attributes["llm.input_messages.0.message.content"]
        == "***REDACTED***"
    )
    assert set(output) == {
        "parsed_json",
        "tool_calls",
        "finish_reason",
    }
    assert output["finish_reason"] == "tool_calls"
    assert output["parsed_json"] == {"ok": True}
    assert output["tool_calls"] == [
        {"id": "call-1", "name": "catalog.lookup"}
    ]
    assert span.attributes["llm.finish_reason"] == "tool_calls"
    assert (
        span.attributes["llm.output_messages.0.message.role"]
        == "assistant"
    )
    assert (
        span.attributes["llm.output_messages.0.message.content"]
        == "done"
    )
    assert (
        span.attributes[
            "llm.output_messages.0.message.tool_calls.0.tool_call.id"
        ]
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


def test_agent_toolbox_uses_actual_tool_name_and_sanitizes_trace_payload() -> None:
    """The Agent-owned execution boundary emits safe tool spans."""
    from opentelemetry.trace.status import StatusCode

    from restscope.capabilities import AgentToolbox, ToolContext
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


def test_app_owns_one_runtime_and_emits_chain_hierarchy(tmp_path: Path) -> None:
    """Scenario: verify that app owns one runtime and emits a CHAIN hierarchy."""
    from restscope import RESTScopeApp
    from restscope.supervisor import RESTScopeRunRequest
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

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
    database = tmp_path / "tracing-app.sqlite"
    env_file.write_text(
        "\n".join(
            [
                "THINK_API_KEY=thinking-secret",
                "FAST_API_KEY=fast-secret",
                f"DB_URL=sqlite:///{database}",
            ]
        ),
        encoding="utf-8",
    )
    runtime, exporter = _recording_runtime()
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(
            tracing_runtime=runtime
        ),
        tracing_runtime=runtime,
    )
    context = app.initialize(
        schema_source={"kind": "inline", "content": json.dumps(schema)},
        base_url="http://example.test",
        headers={"Authorization": "Bearer header-secret"},
    )

    report = app.run(
        RESTScopeRunRequest(
            metadata={"task_id": "trace-task"},
        )
    )
    with runtime.span(
        "test.config-secrets",
        kind="CHAIN",
        input_value={
            "keys": ["thinking-secret", "fast-secret"],
            "authorization": "Bearer header-secret",
        },
    ):
        pass
    app.close()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    app_span = spans["RESTScopeApp.run"]
    graph_span = spans["RESTScopeMainGraph.run"]
    attempt_span = spans["RESTScopeMainGraph.operation_attempt"]
    operation_span = spans["OperationSmokeCoordinator.run"]
    rendered = json.dumps(
        [dict(span.attributes) for span in spans.values()],
        default=str,
    )

    assert app.tracing_runtime is runtime
    assert app.capability_runtime.operation_testing_service.tracing_runtime is runtime
    assert (
        app.tracing_runtime.redactor
        is app.capability_runtime.operation_testing_service.tracing_runtime.redactor
    )
    with pytest.raises(AttributeError):
        app.tracing_runtime = runtime
    assert graph_span.parent.span_id == app_span.context.span_id
    assert attempt_span.parent.span_id == graph_span.context.span_id
    assert operation_span.parent.span_id == attempt_span.context.span_id
    assert app_span.attributes["openinference.span.kind"] == "CHAIN"
    assert graph_span.attributes["openinference.span.kind"] == "CHAIN"
    assert attempt_span.attributes["openinference.span.kind"] == "CHAIN"
    assert operation_span.attributes["openinference.span.kind"] == "CHAIN"
    assert app_span.attributes["restscope.task_id"] == "trace-task"
    assert json.loads(app_span.attributes["output.value"]) == {
        "report_id": report.report_id,
        "status": "passed",
        "stop_reason": "completed",
        "operation_count": 1,
        "attempt_count": 1,
    }
    assert json.loads(graph_span.attributes["output.value"]) == {
        "report_id": report.report_id,
        "status": "passed",
        "stop_reason": "completed",
        "operation_count": 1,
        "attempt_count": 1,
        "rounds": 1,
        "satisfied_operation_count": 1,
        "unattempted_operation_count": 0,
        "disposition_counts": {"satisfied": 1},
        "failure_kind_counts": {},
        "error": None,
    }
    assert app_span.attributes["restscope.output.truncated"] is False
    assert graph_span.attributes["restscope.output.truncated"] is False
    assert "header-secret" in rendered
    assert "thinking-secret" not in rendered
    assert "fast-secret" not in rendered
    assert "tracing" not in report.model_dump(mode="json")
    assert not hasattr(context, "tracing_runtime")


def test_app_rebinds_every_builtin_capability_trace_consumer(tmp_path: Path) -> None:
    """Scenario: verify that app rebinds every builtin capability trace consumer."""
    from restscope import RESTScopeApp
    from restscope.capabilities import build_capabilities
    from restscope.observability import TracingRuntime
    from restscope.redaction import Redactor
    from restscope.restscope_config import RESTScopeConfig
    from restscope.testing import OperationTestingService
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    old_runtime = TracingRuntime.disabled(redactor=Redactor(["old-key"]))
    app_runtime = TracingRuntime.disabled(redactor=Redactor(["app-key"]))
    testing_service = OperationTestingService(
        config_catalog=object(),
        tracing_runtime=old_runtime,
    )
    capabilities = build_capabilities(
        tracing_runtime=old_runtime,
        operation_testing_service=testing_service,
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
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(tmp_path / ".env"),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        capability_runtime=capabilities,
        tracing_runtime=app_runtime,
    )

    assert capabilities.external_tools is not None
    assert capabilities.external_tools.tracing_runtime is app_runtime
    assert capabilities.operation_testing_service is testing_service
    assert testing_service.tracing_runtime is app_runtime
    assert testing_service.tracing_runtime.redactor is app_runtime.redactor

    app.close()
    old_runtime.close()


def test_http_request_tool_keeps_full_result_while_trace_output_is_bounded() -> None:
    """Scenario: verify that http request tool keeps full result while trace output is bounded."""
    import httpx

    from restscope.capabilities import (
        AgentToolbox,
        ToolContext,
        TargetHTTPRequestTool,
        http_request_tool_spec,
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
    http_tool = TargetHTTPRequestTool(
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    context = ToolContext(
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
    span = next(span for span in exporter.get_finished_spans() if span.name == "restscope.http.request")
    assert span.attributes["restscope.output.truncated"] is True
    assert span.attributes["restscope.output.original_size_bytes"] > 65536
    assert "runtime-secret" not in span.attributes["output.value"]


def test_smoke_batch_emits_sanitized_batch_and_case_spans(tmp_path: Path) -> None:
    """Scenario: Smoke's internal batch runner emits sanitized batch and case spans."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import (
        GeneratorConfigCatalog,
        OperationTestingService,
    )

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
                            {"name": "token", "in": "query", "schema": {"type": "string"}}
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /search"]
    node = next(iter(operation.input_nodes.values()))
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'trace-testing.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    from restscope.testing import prepare_accepted_generator_patch

    current = catalog.require_operation(operation.operation_key)
    updated = prepare_accepted_generator_patch(
        current,
        [
            {
                "input_node_id": node.input_node_id,
                "inclusion_probability": 1,
                "strategy": {"type": "constant", "value": "generated-secret"},
            }
        ],
    )
    with catalog.unit_of_work_factory() as uow:
        uow.generator_configs.replace_inputs(
            operation_key=operation.operation_key,
            expected=current.configs,
            updated=updated.configs,
        )
        uow.commit()
    runtime, exporter = _recording_runtime(secret_values=["llm-api-key"])
    service = OperationTestingService(
        config_catalog=catalog,
        tracing_runtime=runtime,
        transport=TargetHTTPTransport(
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

    result = service.run_smoke_batch(
        context,
        operation_key=operation.operation_key,
        case_count=2,
        seed=4,
    )
    runtime.close()

    rendered_result = json.dumps(
        {
            "run_id": result.run_id,
            "operation_key": result.operation_key,
            "seed": result.seed,
            "cases": [
                case.model_dump(mode="json")
                for case in result.cases
            ],
        }
    )
    assert "runtime-secret" not in rendered_result
    assert "runtime-csrf-secret" not in rendered_result
    assert "Authorization" not in rendered_result
    assert "X-CSRF-Token" not in rendered_result
    assert "generated-secret" in rendered_result
    spans = list(exporter.get_finished_spans())
    batch = next(
        span
        for span in spans
        if span.name == "OperationTestingService.run_smoke_batch"
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
