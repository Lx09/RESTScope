"""Regression scenarios for smoke tracking. Each test documents one observable contract or failure boundary."""

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
    """Scenario: verify that supervisor records each smoke attempt as graph child."""
    from restscope.supervisor import RESTScopeMainGraph, RESTScopeRunRequest
    from restscope.operation_smoke import OperationSmokeResult

    class PassingSmoke:
        def run(self, context, request):
            del context
            return OperationSmokeResult(
                status="passed",
                operation_key=request.operation_key,
                success_rate=1,
                required_success_rate=request.success_rate_threshold,
                stop_reason="success_rate_reached",
                reason="The complete Batch passed in this tracing scenario.",
            )

    runtime, exporter = _recording_runtime()
    report = RESTScopeMainGraph(
        operation_smoke_coordinator=PassingSmoke(),
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

    from restscope.api_behavior_monitor import (
        APIBehaviorResponseProcessor,
        build_api_behavior_monitor_coordinator,
    )
    from restscope.operation_smoke import (
        BehaviorMonitorReferenceValues,
        OperationSmokeCoordinator,
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
    monitor = build_api_behavior_monitor_coordinator(
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

    class UnexpectedDedup:
        def deduplicate(self, *args, **kwargs):
            raise AssertionError("a successful batch must not invoke Dedup")

    class UnexpectedFactory:
        def create(self):
            raise AssertionError("a successful batch must not create a sub-Agent")

    class EmptyConstraintReader:
        """Return the empty durable Constraint set used by this success case."""

        def current_constraints(self, operation_key: str):
            """Confirm the requested operation and return no Constraints."""
            assert operation_key == "GET /items"
            return []

    smoke = OperationSmokeCoordinator(
        config_catalog=catalog,
        batch_runner=testing,
        failure_deduplicator=UnexpectedDedup(),
        failure_solver_factory=UnexpectedFactory(),
        constraint_reader=EmptyConstraintReader(),
        reference_values=references,
        tracing_runtime=runtime,
    )
    return ir, smoke, runtime, exporter


def test_smoke_batch_case_and_behavior_tracking_form_one_hierarchy(
    tmp_path: Path,
) -> None:
    """Scenario: verify that smoke batch case and behavior tracking form one hierarchy."""
    from restscope.operation_smoke import OperationSmokeRequest
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
        ),
    )
    runtime.close()

    spans = list(exporter.get_finished_spans())
    smoke_span = next(span for span in spans if span.name == "OperationSmokeCoordinator.run")
    batch_span = next(
        span
        for span in spans
        if span.name == "OperationTestingService.run_smoke_batch"
    )
    cases = [span for span in spans if span.name == "RESTScopeTestCase.execute"]
    monitors = [
        span for span in spans if span.name == "APIBehaviorMonitorCoordinator.observe_response"
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
    run_id = result.batch_run_ids[0]
    assert batch_span.attributes["restscope.test.run_id"] == run_id
    assert all(case.attributes["restscope.test.run_id"] == run_id for case in cases)
    assert all(
        monitor.attributes["restscope.response_contract.status"]
        in {"matched", "already_checked"}
        for monitor in monitors
    )


def test_failure_dedup_uses_its_role_in_llm_trace(
    tmp_path: Path,
) -> None:
    """Semantic Dedup model calls are tagged with the independent role."""
    del tmp_path
    from restscope.operation_smoke.failure_dedup import FailureDedupAgent
    from restscope.operation_smoke.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )
    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.openapi_parser import OpenAPIParser
    from restscope.llm import (
        LLMClient,
        LLMModelConfig,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
    )
    from restscope.llm.providers.base import BaseLLMProvider

    class DedupProvider(BaseLLMProvider):
        name = "stub"

        def invoke(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model=request.model,
                parsed_json={
                    "failures": [
                        {
                            "summary": "Two name errors.",
                            "suspected_parameters": ["body.name"],
                            "messages": ["first", "second"],
                        }
                    ],
                    "reason": "Both involve body.name.",
                },
            )

    registry = LLMProviderRegistry()
    registry.register(DedupProvider())
    runtime, exporter = _recording_runtime()

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Dedup trace", "version": "1"},
            "paths": {
                "/items": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"}
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"400": {"description": "bad"}},
                    }
                }
            },
        }
    )
    capabilities = build_capabilities(tracing_runtime=runtime)
    capabilities.bind_context(
        ToolContext(ir=ir, baseline_schema_source={})
    )
    from restscope.request_inputs import RequestInputReference

    catalog = TestCaseCatalog(
        input_references=[
            RequestInputReference.body(),
            RequestInputReference.body().property("name"),
        ]
    )
    for message in ("first", "second"):
        catalog.record(
            CatalogTestCaseDraft(
                request={
                    "path": {},
                    "query": {},
                    "header": {},
                    "cookie": {},
                    "body": {"name": message},
                },
                response_body={"message": message},
                failure=HTTPFailure(
                    status_code=400,
                    messages=[message],
                ),
            )
        )
    agent = FailureDedupAgent(
        client=LLMClient(registry, tracing_runtime=runtime),
        model=LLMModelConfig(
            role="operation_smoke_failure_dedup",
            provider="stub",
            model="think",
            enabled=True,
        ),
        openapi_capability=capabilities.openapi_capability,
        tracing_runtime=runtime,
    )
    result, outputs, corrections, errors = agent.deduplicate(
        operation_key="POST /items",
        semantic_parameters=["body.name"],
        observations=[
            {"message": "first", "case_id": "TC1"},
            {"message": "second", "case_id": "TC2"},
        ],
        catalog=catalog,
        max_outputs=1,
    )
    runtime.close()

    spans = list(exporter.get_finished_spans())
    llm_span = next(span for span in spans if span.name == "LLMClient.invoke")
    assert result is not None
    assert (outputs, corrections, errors) == (1, 0, [])
    assert llm_span.attributes["llm.model_name"] == "think"
