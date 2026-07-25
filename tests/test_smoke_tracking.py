from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest


pytest.importorskip("opentelemetry.sdk")


def _recording_runtime():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        )
    )
    return runtime, exporter


def _supervisor_context():
    from restscope.capabilities import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Supervisor tracking", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    return ToolContext(
        ir=OpenAPIParser.parse(spec),
        baseline_schema_source={
            "kind": "inline",
            "format": "json",
            "content": json.dumps(spec),
        },
        base_url="https://api.example.test",
    )


def test_supervisor_records_each_smoke_attempt_as_graph_child() -> None:
    from restscope.agent import RESTScopeMainGraph, RESTScopeRunRequest
    from restscope.agent.operation_smoke import OperationSmokeResult

    class PassingSmoke:
        def run(self, context, request):
            del context
            return OperationSmokeResult(
                status="passed",
                operation_key=request.operation_key,
                success_rate=1,
                required_success_rate=request.success_rate_threshold,
                active_config_revision=1,
            )

    runtime, exporter = _recording_runtime()
    report = RESTScopeMainGraph(
        operation_smoke_agent=PassingSmoke(),
        tool_context=_supervisor_context(),
        tracing_runtime=runtime,
    ).run(RESTScopeRunRequest(metadata={"task_id": "tracking-task"}))
    runtime.close()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    graph = spans["RESTScopeMainGraph.run"]
    attempt = spans["RESTScopeMainGraph.operation_attempt"]

    assert report.status == "passed"
    assert attempt.parent.span_id == graph.context.span_id
    assert attempt.attributes["restscope.task_id"] == "tracking-task"
    assert attempt.attributes["restscope.operation.key"] == "GET /items"
    assert attempt.attributes["restscope.operation.round"] == 1
    assert attempt.attributes["restscope.operation.attempt"] == 1
    assert attempt.attributes["restscope.operation.disposition"] == "satisfied"


def _smoke_runtime(tmp_path: Path):
    import httpx

    from restscope.agent.api_behavior_monitor import (
        APIBehaviorResponseProcessor,
        build_api_behavior_monitor_agent,
    )
    from restscope.agent.operation_smoke import (
        BehaviorMonitorReferenceValues,
        OperationSmokeAgent,
    )
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.restscope_config import DBConfig, RESTScopeConfig
    from restscope.testing import GeneratorConfigCatalog, OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Smoke tracking", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "operationId": "listItems",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )
    database = tmp_path / "smoke-tracking.sqlite"
    engine = create_engine_from_url(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
    )
    assert catalog.initialize_once(ir) is True
    runtime, exporter = _recording_runtime()
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / ".env"),
        db=DBConfig(url=f"sqlite:///{database}"),
    )
    monitor = build_api_behavior_monitor_agent(
        config,
        tracing_runtime=runtime,
    )
    references = BehaviorMonitorReferenceValues(monitor)
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b"{}",
                )
            ),
            **kwargs,
        ),
        response_processor=APIBehaviorResponseProcessor(monitor),
    )
    testing = OperationTestingService(
        config_catalog=catalog,
        transport=transport,
        tracing_runtime=runtime,
        reference_values=references,
    )

    class UnexpectedDiagnoser:
        def diagnose(self, **kwargs):
            raise AssertionError("a successful batch must not invoke diagnosis")

    smoke = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=testing,
        diagnoser=UnexpectedDiagnoser(),
        reference_values=references,
        tracing_runtime=runtime,
    )
    return ir, smoke, runtime, exporter


def test_smoke_batch_case_and_behavior_tracking_form_one_hierarchy(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest
    from restscope.capabilities import ToolContext

    ir, smoke, runtime, exporter = _smoke_runtime(tmp_path)
    result = smoke.run(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        OperationSmokeRequest(
            operation_key="GET /items",
            case_count=2,
            seed=7,
        ),
    )
    runtime.close()

    spans = list(exporter.get_finished_spans())
    smoke_span = next(span for span in spans if span.name == "OperationSmokeAgent.run")
    batch_span = next(
        span for span in spans if span.name == "OperationTestingService.run_operation"
    )
    cases = [span for span in spans if span.name == "RESTScopeTestCase.execute"]
    monitors = [
        span for span in spans if span.name == "APIBehaviorMonitorAgent.observe_response"
    ]
    resources = [
        span for span in spans if span.name == "ResourceIdentifierTracker.observe"
    ]

    assert result.status == "passed"
    assert batch_span.parent.span_id == smoke_span.context.span_id
    assert len(cases) == len(monitors) == len(resources) == 2
    assert all(span.parent.span_id == batch_span.context.span_id for span in cases)
    assert all(
        monitor.parent.span_id == case.context.span_id
        for case, monitor in zip(cases, monitors, strict=True)
    )
    assert all(
        resource.parent.span_id == monitor.context.span_id
        for monitor, resource in zip(monitors, resources, strict=True)
    )
    run_id = result.batch_reports[0].run_id
    assert batch_span.attributes["restscope.test.run_id"] == run_id
    assert all(case.attributes["restscope.test.run_id"] == run_id for case in cases)
    assert all(
        monitor.attributes["restscope.response_contract.status"]
        in {"matched", "already_checked"}
        for monitor in monitors
    )


def test_smoke_diagnosis_groups_fast_llm_calls_under_agent_span(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeDiagnoser
    from restscope.llm import (
        LLMClient,
        LLMModelConfig,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
    )
    from restscope.llm.providers.base import BaseLLMProvider
    from restscope.testing import (
        BatchFailureReport,
        OperationExecutionReport,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        UniqueFailureMessage,
    )

    class DiagnosisProvider(BaseLLMProvider):
        name = "stub"

        def invoke(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json={
                    "no_parameter_issue": True,
                    "suspects": [],
                },
            )

    registry = LLMProviderRegistry()
    registry.register(DiagnosisProvider())
    runtime, exporter = _recording_runtime()
    diagnoser = OperationSmokeDiagnoser(
        client=LLMClient(registry, tracing_runtime=runtime),
        model=LLMModelConfig(
            role="operation_smoke_parameter_diagnosis",
            provider="stub",
            model="fast",
            enabled=True,
        ),
        tracing_runtime=runtime,
    )
    report = OperationExecutionReport(
        run_id="run_failure",
        operation_key="GET /items",
        seed=1,
        config_revision=1,
        status="completed",
        cases=[],
        status_code_counts={"400": 1},
        error_count=0,
        observed_2xx=False,
        failure_report=BatchFailureReport(
            unique_failure_messages=[
                UniqueFailureMessage(
                    failure_id="failure_1",
                    message="HTTP 400: invalid input",
                    case_ids=["case_1"],
                )
            ]
        ),
    )
    config = OperationGeneratorConfig(
        operation_key="GET /items",
        revision=1,
        enabled=True,
        snapshot=OperationTestSnapshot(
            operation_key="GET /items",
            method="GET",
            path="/items",
            parameters=[],
            input_nodes=[],
        ),
        configs=[],
    )

    result = diagnoser.diagnose(report=report, config=config)
    runtime.close()

    spans = list(exporter.get_finished_spans())
    diagnosis_span = next(
        span for span in spans if span.name == "OperationSmokeDiagnoser.diagnose"
    )
    llm_span = next(span for span in spans if span.name == "LLMClient.invoke")
    assert result.diagnosis.no_parameter_issue is True
    assert llm_span.parent.span_id == diagnosis_span.context.span_id
    assert diagnosis_span.attributes["restscope.operation.key"] == "GET /items"
    assert diagnosis_span.attributes["restscope.test.run_id"] == "run_failure"
