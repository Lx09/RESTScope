from __future__ import annotations

from pathlib import Path

import pytest


class CapturingProcessor:
    def __init__(self, *, warning=None) -> None:
        self.warning = warning
        self.calls = []

    def process(self, observation, context):
        self.calls.append((observation, context))
        return self.warning


def _testing_service(
    tmp_path: Path,
    *,
    processor,
    response_content: bytes,
    status_code: int = 200,
):
    import httpx

    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog, OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "monitor", "version": "1"},
            "paths": {
                "/users/{userId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer", "minimum": 1},
                            }
                        ],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"id": {"type": "integer"}},
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
        }
    )
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'transport.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    status_code,
                    headers={"Content-Type": "application/json"},
                    content=response_content,
                )
            ),
            **kwargs,
        ),
        response_processor=processor,
    )
    return ir, OperationTestingService(config_catalog=catalog, transport=transport)


def test_operation_testing_supplies_known_operation_and_body_to_processor(
    tmp_path: Path,
) -> None:
    from restscope.capabilities import ToolContext

    processor = CapturingProcessor()
    ir, service = _testing_service(
        tmp_path,
        processor=processor,
        response_content=b'{"id":7}',
    )

    report = service.run_operation(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        operation_key="GET /users/{userId}",
        seed=1,
    )

    assert report.status == "completed"
    assert len(processor.calls) == 1
    observation, context = processor.calls[0]
    assert observation.body == b'{"id":7}'
    assert observation.body_truncated is False
    assert context.operation_key == "GET /users/{userId}"
    assert context.operation_method == "GET"
    assert context.operation_path == "/users/{userId}"
    assert context.ir is ir
    assert report.cases[0].behavior_monitor_warnings == []


def test_processor_warning_does_not_replace_raw_http_result(tmp_path: Path) -> None:
    import httpx

    from restscope.capabilities import ToolContext, ToolRegistry, register_http_request_tool
    from restscope.http_transport import (
        TargetHTTPTransport,
        TargetResponseProcessorWarning,
    )
    from restscope.openapi_parser import OpenAPIParser

    processor = CapturingProcessor(
        warning=TargetResponseProcessorWarning(
            code="operation_match_ambiguous",
            message="Request matches multiple operations",
            issues=("GET /users/{id}", "GET /{entity}/7"),
        )
    )
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b'{"id":7}',
                )
            ),
            **kwargs,
        ),
        response_processor=processor,
    )
    registry = ToolRegistry()
    register_http_request_tool(registry, transport=transport)
    handler = registry.get_handler("restscope.http.request")
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "raw", "version": "1"},
            "paths": {
                "/users/{id}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )

    result = handler(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        method="GET",
        path="/users/7",
    )

    assert result["structured"]["status_code"] == 200
    assert result["structured"]["body"] == {"id": 7}
    assert result["structured"]["response_validation"] == "partial"
    assert result["structured"]["behavior_monitor_warnings"] == [
        {
            "code": "operation_match_ambiguous",
            "message": "Request matches multiple operations",
            "issues": ["GET /users/{id}", "GET /{entity}/7"],
        }
    ]
    assert processor.calls[0][1].operation_key is None
    assert processor.calls[0][1].ir is ir


def test_operation_smoke_probe_pins_exact_operation_context_without_leaking(
    tmp_path: Path,
) -> None:
    del tmp_path
    import httpx

    from restscope.agent.operation_smoke.probe import CurrentOperationHTTPProbe
    from restscope.capabilities import (
        ToolCallValidator,
        ToolContext,
        ToolExecutor,
        ToolPolicy,
        ToolRegistry,
        register_http_request_tool,
    )
    from restscope.http_transport import TargetHTTPTransport
    from restscope.llm import ToolCall
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.snapshot import build_initial_operation_config

    processor = CapturingProcessor()
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b'{"id":7}',
                )
            ),
            **kwargs,
        ),
        response_processor=processor,
    )
    registry = ToolRegistry()
    spec = register_http_request_tool(registry, transport=transport)
    executor = ToolExecutor(
        registry,
        ToolCallValidator(registry, ToolPolicy()),
    )
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "route collision", "version": "1"},
            "paths": {
                "/users/me": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
                "/users/{userId}": {
                    "get": {
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
    )
    executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        )
    )
    config = build_initial_operation_config(
        ir.operations["GET /users/{userId}"]
    )
    probe = CurrentOperationHTTPProbe(executor)

    result = probe.execute(
        config=config,
        tool_call=ToolCall(
            id="scoped",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/users/me"},
        ),
    )
    unscoped = executor.execute(
        tool_call=ToolCall(
            id="unscoped",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/users/me"},
        ),
        role="future_agent",
        state={},
    )

    assert result.status == "succeeded"
    assert unscoped.status == "succeeded"
    scoped_context = processor.calls[0][1]
    assert scoped_context.operation_key == "GET /users/{userId}"
    assert scoped_context.operation_method == "GET"
    assert scoped_context.operation_path == "/users/{userId}"
    assert processor.calls[1][1].operation_key is None
    assert "operation_key" not in spec.input_schema["properties"]


def test_target_operation_scope_resets_after_exception() -> None:
    from restscope.http_transport import (
        TargetOperationIdentity,
        current_target_operation_identity,
        target_operation_scope,
    )

    identity = TargetOperationIdentity(
        operation_key="GET /users/{userId}",
        method="GET",
        path="/users/{userId}",
    )

    with pytest.raises(RuntimeError, match="probe failed"):
        with target_operation_scope(identity):
            assert current_target_operation_identity() == identity
            raise RuntimeError("probe failed")

    assert current_target_operation_identity() is None


def test_operation_testing_truncates_monitor_body_at_one_mib(
    tmp_path: Path,
) -> None:
    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetResponseProcessorWarning

    processor = CapturingProcessor(
        warning=TargetResponseProcessorWarning(
            code="resource_monitor_body_truncated",
            message="Response body was truncated",
        )
    )
    content = b'{"value":"' + (b"x" * (1024 * 1024)) + b'"}'
    ir, service = _testing_service(
        tmp_path,
        processor=processor,
        response_content=content,
    )

    report = service.run_operation(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        operation_key="GET /users/{userId}",
        seed=1,
    )

    observation, _context = processor.calls[0]
    assert len(observation.body) == 1024 * 1024
    assert observation.body_truncated is True
    assert report.cases[0].response is not None
    assert report.cases[0].behavior_monitor_warnings[0].code == (
        "resource_monitor_body_truncated"
    )


def test_operation_testing_buffers_and_monitors_non_2xx_response_once(
    tmp_path: Path,
) -> None:
    from restscope.capabilities import ToolContext

    processor = CapturingProcessor()
    ir, service = _testing_service(
        tmp_path,
        processor=processor,
        response_content=b'{"error":"missing"}',
        status_code=404,
    )

    report = service.run_operation(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        operation_key="GET /users/{userId}",
        seed=1,
    )

    assert report.cases[0].response is not None
    assert report.cases[0].response.status_code == 404
    assert len(processor.calls) == 1
    observation, context = processor.calls[0]
    assert observation.status_code == 404
    assert observation.body == b'{"error":"missing"}'
    assert context.operation_key == "GET /users/{userId}"


def _resource_monitor(tmp_path: Path):
    from restscope.agent.api_behavior_monitor import (
        APIBehaviorMonitorAgent,
        ResourceCatalog,
        ResourceIdentifierTracker,
        ResponseContractTracker,
        ResponseValueCatalog,
        ResponseValueTracker,
    )
    from restscope.db import (
        Base,
        SqlAlchemyResourceCatalogUnitOfWork,
        SqlAlchemyResponseValueCatalogUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.llm import LLMModelConfig

    class UnexpectedLLM:
        def invoke(self, request):
            raise AssertionError(f"unexpected LLM request: {request}")

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'resource-monitor.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    catalog = ResourceCatalog(
        lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
    )
    resource_tracker = ResourceIdentifierTracker(
        catalog=catalog,
        client=UnexpectedLLM(),
        model=LLMModelConfig(
            role="api_behavior_monitor",
            provider="stub",
            model="fast",
        ),
    )
    response_value_catalog = ResponseValueCatalog(
        lambda: SqlAlchemyResponseValueCatalogUnitOfWork(session_factory)
    )
    return (
        APIBehaviorMonitorAgent(
            contract_tracker=ResponseContractTracker(),
            resource_identifier_tracker=resource_tracker,
            response_value_tracker=ResponseValueTracker(
                catalog=response_value_catalog
            ),
        ),
        catalog,
    )


def test_raw_http_matches_operation_before_synchronously_updating_catalog(
    tmp_path: Path,
) -> None:
    import httpx

    from restscope.agent.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.capabilities import ToolContext, ToolRegistry, register_http_request_tool
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser

    agent, catalog = _resource_monitor(tmp_path)
    processor = APIBehaviorResponseProcessor(agent)
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b'{"id":7,"name":"Ada"}',
                )
            ),
            **kwargs,
        ),
        response_processor=processor,
    )
    registry = ToolRegistry()
    register_http_request_tool(registry, transport=transport)
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "raw", "version": "1"},
            "paths": {
                "/users/{userId}": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"}
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )

    result = registry.get_handler("restscope.http.request")(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        method="GET",
        path="/users/7",
    )

    assert result["structured"]["body"] == {"id": 7, "name": "Ada"}
    assert result["structured"]["behavior_monitor_warnings"] == []
    lookup = catalog.lookup(ResourceLookupRequest(resource="user"))
    assert lookup.recommended_id == 7
    assert lookup.operations[0].operation_key == "GET /users/{userId}"


def test_raw_http_ambiguous_operation_match_warns_without_catalog_write(
    tmp_path: Path,
) -> None:
    import httpx

    from restscope.agent.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.capabilities import ToolContext, ToolRegistry, register_http_request_tool
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser

    agent, catalog = _resource_monitor(tmp_path)
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b'{"id":7}',
                )
            ),
            **kwargs,
        ),
        response_processor=APIBehaviorResponseProcessor(agent),
    )
    registry = ToolRegistry()
    register_http_request_tool(registry, transport=transport)
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "ambiguous", "version": "1"},
            "paths": {
                "/teams/{member}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
                "/{collection}/me": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                },
            },
        }
    )

    result = registry.get_handler("restscope.http.request")(
        ToolContext(
            ir=ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": "{}",
            },
            base_url="https://api.example.test",
        ),
        method="GET",
        path="/teams/me",
    )

    assert result["structured"]["status_code"] == 200
    assert result["structured"]["behavior_monitor_warnings"][0]["code"] == (
        "operation_match_ambiguous"
    )
    assert catalog.lookup(ResourceLookupRequest(resource="team")).status == "not_found"


def test_resolved_operation_monitor_error_is_persisted_and_later_cleared(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        MonitoredOperation,
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
        ResourceObservation,
    )
    from restscope.http_transport import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )
    from restscope.openapi_parser import OpenAPIParser

    agent, catalog = _resource_monitor(tmp_path)
    agent.resource_identifier_tracker.observe(
        ResourceObservation(
            operation=MonitoredOperation(
                operation_key="GET /users/{userId}",
                method="GET",
                path="/users/{userId}",
            ),
            status_code=200,
            media_type="application/json",
            body={"id": 7},
        )
    )
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "monitor-errors", "version": "1"},
            "paths": {
                "/users/{userId}": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    processor = APIBehaviorResponseProcessor(agent)
    context = TargetResponseOperationContext(ir=ir)
    invalid = TargetResponseObservation(
        method="GET",
        path="/users/7",
        url="https://api.example.test/users/7",
        status_code=200,
        reason_phrase="OK",
        headers={"content-type": "application/json"},
        body=b"{invalid",
        body_truncated=False,
    )

    outcome = processor.process(invalid, context)

    assert outcome.response_validation == "partial"
    assert outcome.warnings[0].code == "response_contract_pending_retry"
    assert catalog.lookup(ResourceLookupRequest(resource="user")).errors == []

    recovered = processor.process(
        invalid.__class__(
            method=invalid.method,
            path=invalid.path,
            url=invalid.url,
            status_code=invalid.status_code,
            reason_phrase=invalid.reason_phrase,
            headers=invalid.headers,
            body=b'{"id":7}',
            body_truncated=False,
        ),
        context,
    )
    assert recovered.response_validation == "evaluated"
    assert recovered.warnings == ()
    assert catalog.lookup(ResourceLookupRequest(resource="user")).errors == []


def test_schema_only_dotted_property_fails_closed_without_learning_selector(
    tmp_path: Path,
) -> None:
    from restscope.agent.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.http_transport import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )
    from restscope.openapi_parser import OpenAPIParser

    agent, catalog = _resource_monitor(tmp_path)
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "dotted-property", "version": "1"},
            "paths": {
                "/commits/{commitId}": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "bad.key": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )
    processor = APIBehaviorResponseProcessor(agent)
    observation = TargetResponseObservation(
        method="GET",
        path="/commits/abc123",
        url="https://api.example.test/commits/abc123",
        status_code=200,
        reason_phrase="OK",
        headers={"content-type": "application/json"},
        body=b'{"message":"schema-only dotted property"}',
        body_truncated=False,
    )
    context = TargetResponseOperationContext(ir=ir)

    first = processor.process(observation, context)
    second = processor.process(observation, context)

    assert first.warnings[0].code == "resource_monitor_evidence_limit_exceeded"
    assert second.warnings[0].code == "resource_monitor_evidence_limit_exceeded"
    assert catalog.list_rules("GET /commits/{commitId}") == []
    assert catalog.lookup(ResourceLookupRequest(resource="commit")).status == "not_found"


def test_response_schema_fields_include_collection_item_resource_name() -> None:
    from restscope.agent.api_behavior_monitor.agent import _response_schema_fields
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "schema-name", "version": "1"},
            "paths": {
                "/dashboard": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "data": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "title": "Assignment",
                                                        "properties": {
                                                            "id": {
                                                                "type": "integer"
                                                            },
                                                            "label": {
                                                                "type": "string"
                                                            },
                                                        },
                                                    },
                                                }
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
    )

    fields = _response_schema_fields(
        ir.operations["GET /dashboard"],
        status_code=200,
        media_type="application/json",
    )

    assert {item["selector"] for item in fields} == {
        "$.data[].id",
        "$.data[].label",
    }
    assert {item["resource_name"] for item in fields} == {"Assignment"}


def test_default_app_uses_one_monitored_transport_and_registers_lookup_tool(
    tmp_path: Path,
) -> None:
    from restscope import RESTScopeApp
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_stub import PassingOperationSmokeAgent

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_URL=sqlite:///{tmp_path / 'app.sqlite'}\n",
        encoding="utf-8",
    )
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_agent=PassingOperationSmokeAgent(),
    )
    try:
        runtime = app.capability_runtime
        service = runtime.operation_testing_service
        assert service is not None
        raw_handler = runtime.tool_registry.get_handler("restscope.http.request")
        raw_tool = raw_handler.__self__

        assert runtime.api_behavior_monitor_agent is not None
        assert not hasattr(runtime.api_behavior_monitor_agent, "initialize")
        assert service.transport is raw_tool.transport
        assert service.transport.response_processor.agent is (
            runtime.api_behavior_monitor_agent
        )
        assert runtime.tool_registry.get_spec("restscope.resource.lookup").read_only is True
    finally:
        app.close()
