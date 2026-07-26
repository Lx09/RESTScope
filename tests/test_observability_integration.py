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


def test_operation_smoke_trace_contains_task_cards_not_internal_models() -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.agent.operation_smoke import PlanSolveDiagnosisResult
    from restscope.agent.parameter_patch import (
        GeneratorPatchAttribution,
        GeneratorPatchDraft,
        ValidatedPatchGroup,
    )
    from restscope.llm import (
        LLMClient,
        LLMModelConfig,
        LLMProviderRegistry,
        LLMResponse,
    )
    from restscope.llm.providers.base import BaseLLMProvider
    from tests._operation_smoke_plan_solve_fixtures import (
        smoke_config,
        smoke_report,
    )

    runtime, exporter = _recording_runtime()
    responses = [
        LLMResponse(
            provider="stub",
            model="think-model",
            parsed_json={
                "action": "ready",
                "cause": "The generated identifier was rejected.",
                "solutions": [
                    {
                        "input": "path.projectId",
                        "desired_behavior": "Use a known project ID.",
                    }
                ],
                "evidence_refs": ["F1", "C1"],
                "interaction_notes": [],
            },
        ),
        LLMResponse(
            provider="stub",
            model="think-model",
            parsed_json={
                "items": [
                    {
                        "item_id": "F1",
                        "status": "persisting",
                        "current_failure_refs": ["F1"],
                        "reason": "The identifier failure remains.",
                        "confidence": 0.8,
                    }
                ]
            },
        ),
    ]

    class PromptProvider(BaseLLMProvider):
        name = "stub"

        def invoke(self, request):
            return responses.pop(0)

    registry = LLMProviderRegistry()
    registry.register(PromptProvider())
    client = LLMClient(registry, tracing_runtime=runtime)

    diagnoser = OperationSmokeDiagnoser(
        client=client,
        planning_model=LLMModelConfig(
            role="operation_smoke_root_cause_diagnosis",
            provider="stub",
            model="think-model",
        ),
        effect_model=LLMModelConfig(
            role="operation_smoke_effect_validation",
            provider="stub",
            model="think-model",
        ),
        tracing_runtime=runtime,
    )
    diagnosis = diagnoser.diagnose(
        report=smoke_report(),
        config=smoke_config(),
    )
    group = ValidatedPatchGroup(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        patch=GeneratorPatchDraft(
            updates=[
                {
                    "input_node_id": "path/projectId",
                    "strategy": {
                        "type": "constant",
                        "value": "known-project",
                    },
                }
            ],
            attributions=[
                GeneratorPatchAttribution(
                    input_node_id="path/projectId",
                    group_ids=["G1"],
                    item_ids=["I1"],
                    root_failure_refs=["F1"],
                )
            ],
        ),
        samples=[{"path.projectId": "known-project"} for _ in range(10)],
        attempts=2,
    )
    assert isinstance(diagnosis, PlanSolveDiagnosisResult)
    diagnoser.validate_effect(
        baseline_report=smoke_report(),
        candidate_report=smoke_report().model_copy(
            update={"run_id": "candidate_run"}
        ),
        diagnosis=diagnosis,
        groups=[group],
    )
    runtime.close()

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "OperationSmokeDiagnoser.validate_effect" in span_names
    assert span_names.count("LLMClient.invoke") == 2
    rendered = json.dumps(
        [dict(span.attributes) for span in exporter.get_finished_spans()],
        default=str,
    )
    assert "path.projectId" in rendered
    assert "random-123" in rendered
    for forbidden in (
        "$defs",
        "input_node_id",
        "reference_option_id",
        "config_revision",
        "Authorization",
        "PreparedRequestSummary",
        "RandomStringGenerator",
    ):
        assert forbidden not in rendered


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
    capabilities = build_capabilities(tracing_runtime=runtime)
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
                "arguments": arguments,
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
            arguments={
                "token": "tool-secret",
                "password": "visible-generated-password",
            },
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
    assert result.structured["arguments"] == {
        "token": "***REDACTED***",
        "password": "visible-generated-password",
    }
    assert "visible-generated-password" in rendered
    assert spans["test.fail"].status.status_code is StatusCode.ERROR


def test_app_owns_one_runtime_and_emits_chain_agent_hierarchy(tmp_path: Path) -> None:
    from restscope import RESTScopeApp
    from restscope.agent import RESTScopeRunRequest
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_stub import PassingOperationSmokeAgent

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
        operation_smoke_agent=PassingOperationSmokeAgent(
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
    operation_span = spans["OperationSmokeAgent.run"]
    rendered = json.dumps(
        [dict(span.attributes) for span in spans.values()],
        default=str,
    )

    assert app.tracing_runtime is runtime
    assert app.capability_runtime.tool_executor.tracing_runtime is runtime
    assert (
        app.tracing_runtime.redactor
        is app.capability_runtime.tool_executor.tracing_runtime.redactor
    )
    with pytest.raises(AttributeError):
        app.tracing_runtime = runtime
    assert graph_span.parent.span_id == app_span.context.span_id
    assert attempt_span.parent.span_id == graph_span.context.span_id
    assert operation_span.parent.span_id == attempt_span.context.span_id
    assert app_span.attributes["openinference.span.kind"] == "CHAIN"
    assert graph_span.attributes["openinference.span.kind"] == "AGENT"
    assert attempt_span.attributes["openinference.span.kind"] == "AGENT"
    assert operation_span.attributes["openinference.span.kind"] == "AGENT"
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
    from restscope import RESTScopeApp
    from restscope.capabilities import build_capabilities
    from restscope.observability import TracingRuntime
    from restscope.redaction import Redactor
    from restscope.restscope_config import RESTScopeConfig
    from restscope.testing import OperationTestingService
    from tests._operation_smoke_stub import PassingOperationSmokeAgent

    old_runtime = TracingRuntime.disabled(redactor=Redactor(["old-key"]))
    app_runtime = TracingRuntime.disabled(redactor=Redactor(["app-key"]))
    testing_service = OperationTestingService(
        config_catalog=object(),
        tracing_runtime=old_runtime,
    )
    capabilities = build_capabilities(
        tracing_runtime=old_runtime,
        generator_config_catalog=object(),
        operation_testing_service=testing_service,
    )
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(tmp_path / ".env"),
        operation_smoke_agent=PassingOperationSmokeAgent(),
        capability_runtime=capabilities,
        tracing_runtime=app_runtime,
    )

    assert capabilities.tool_executor.tracing_runtime is app_runtime
    assert capabilities.operation_testing_service is testing_service
    assert testing_service.tracing_runtime is app_runtime
    assert testing_service.tracing_runtime.redactor is app_runtime.redactor

    app.close()
    old_runtime.close()


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
    capabilities = build_capabilities(tracing_runtime=runtime)
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


def test_generated_operation_tool_emits_sanitized_batch_and_case_spans(tmp_path: Path) -> None:
    import httpx

    from restscope.capabilities import RUN_OPERATION_TOOL_NAME, ToolContext, build_capabilities
    from restscope.capabilities.testing_tools import REPLACE_GENERATORS_TOOL_NAME
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.http_transport import TargetHTTPTransport
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import (
        GeneratorConfigCatalog,
        InputGeneratorConfig,
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
    catalog.patch_operation(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node.input_node_id,
                "inclusion_probability": 1,
                "strategy": {"type": "constant", "value": "generated-secret"},
            }
        ],
    )
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
    capabilities = build_capabilities(
        tracing_runtime=runtime,
        generator_config_catalog=catalog,
        operation_testing_service=service,
    )
    capabilities.tool_executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={
                "Authorization": "Bearer runtime-secret",
                "X-Configured-Key": "llm-api-key",
            },
        )
    )

    denied_config_result = capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="config-trace",
            name=REPLACE_GENERATORS_TOOL_NAME,
            arguments={
                "operation_key": operation.operation_key,
                "expected_revision": 2,
                "active_media_type": None,
                "configs": [
                    {
                        "input_node_id": node.input_node_id,
                        "inclusion_probability": 1,
                        "strategy": {
                            "type": "constant",
                            "value": "visible-config-value",
                        },
                    }
                ],
            },
        ),
        role="planner",
        state={},
    )
    result = capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="generated-trace",
            name=RUN_OPERATION_TOOL_NAME,
            arguments={"operation_key": operation.operation_key, "case_count": 2, "seed": 4},
        ),
        role="planner",
        state={},
    )
    runtime.close()

    assert denied_config_result.status == "denied"
    assert result.status == "succeeded"
    rendered_result = result.model_dump_json()
    assert "runtime-secret" in rendered_result
    assert "generated-secret" in rendered_result
    spans = list(exporter.get_finished_spans())
    wrapper = next(span for span in spans if span.name == RUN_OPERATION_TOOL_NAME)
    batch = next(
        span
        for span in spans
        if span.name == "OperationTestingService.run_operation"
    )
    cases = [span for span in spans if span.name == "RESTScopeTestCase.execute"]
    assert len(cases) == 2
    assert batch.parent.span_id == wrapper.context.span_id
    assert all(span.parent.span_id == batch.context.span_id for span in cases)
    rendered_spans = json.dumps([dict(span.attributes) for span in spans], default=str)
    assert "runtime-secret" in rendered_spans
    assert "generated-secret" in rendered_spans
    assert "visible-config-value" in rendered_spans
    assert "llm-api-key" not in rendered_result
    assert "llm-api-key" not in rendered_spans
