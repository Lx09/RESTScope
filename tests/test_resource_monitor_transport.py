"""Regression scenarios for resource monitor transport. Each test documents one observable contract or failure boundary."""

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

    from restscope.target_http import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.harness.operation_testing import OperationTestingService

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
    catalog = RequestGenerationConfigStore()
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
    return ir, OperationTestingService(config_store=catalog, transport=transport)


def test_operation_testing_supplies_known_operation_and_body_to_processor(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing supplies known operation and body to processor."""
    from restscope.tools.context import ToolContext

    processor = CapturingProcessor()
    ir, service = _testing_service(
        tmp_path,
        processor=processor,
        response_content=b'{"id":7}',
    )

    batch = service.run_batch(
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

    assert batch.success_count == 1
    assert len(processor.calls) == 1
    observation, context = processor.calls[0]
    assert observation.body == b'{"id":7}'
    assert observation.body_truncated is False
    assert context.operation_key == "GET /users/{userId}"
    assert context.operation_method == "GET"
    assert context.operation_path == "/users/{userId}"
    assert context.ir is ir
    assert batch.cases[0].failure is None


def test_processor_warning_does_not_replace_raw_http_result(tmp_path: Path) -> None:
    """Scenario: verify that processor warning does not replace raw http result."""
    import httpx

    from restscope.tools.context import ToolContext
    from restscope.tools.http import TargetHTTPRequestTool
    from restscope.target_http import (
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
    http_tool = TargetHTTPRequestTool(transport=transport)
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

    result = http_tool.execute(
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


def test_operation_testing_truncates_monitor_body_at_one_mib(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing truncates monitor body at one mib."""
    from restscope.tools.context import ToolContext
    from restscope.target_http import TargetResponseProcessorWarning

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

    batch = service.run_batch(
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
    assert batch.cases[0].failure is None
    assert processor.warning.code == "resource_monitor_body_truncated"


def test_operation_testing_buffers_and_monitors_non_2xx_response_once(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing buffers and monitors non successful 2xx  response once."""
    from restscope.tools.context import ToolContext

    processor = CapturingProcessor()
    ir, service = _testing_service(
        tmp_path,
        processor=processor,
        response_content=b'{"error":"missing"}',
        status_code=404,
    )

    batch = service.run_batch(
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

    assert batch.cases[0].failure is not None
    assert batch.cases[0].failure.status_code == 404
    assert not hasattr(batch.cases[0], "response_body")
    assert batch.cases[0].failure.messages == ["HTTP 404: missing"]
    assert len(processor.calls) == 1
    observation, context = processor.calls[0]
    assert observation.status_code == 404
    assert observation.body == b'{"error":"missing"}'
    assert context.operation_key == "GET /users/{userId}"


def _resource_monitor(tmp_path: Path):
    from restscope.api_behavior_monitor import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
    from restscope.api_behavior_monitor.resource_identifiers.tracker import ResourceIdentifierTracker
    from restscope.api_behavior_monitor.response_contracts import ResponseContractTracker
    from restscope.api_behavior_monitor.response_values.catalog import ResponseValueCatalog
    from restscope.api_behavior_monitor.response_values.tracker import ResponseValueTracker
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
        APIBehaviorMonitorCoordinator(
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
    """Scenario: verify that raw http matches operation before synchronously updating catalog."""
    import httpx

    from restscope.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.tools.context import ToolContext
    from restscope.tools.http import TargetHTTPRequestTool
    from restscope.target_http import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser

    coordinator, catalog = _resource_monitor(tmp_path)
    processor = APIBehaviorResponseProcessor(coordinator)
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
    http_tool = TargetHTTPRequestTool(transport=transport)
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

    result = http_tool.execute(
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
    """Scenario: verify that raw http ambiguous operation match warns without catalog write."""
    import httpx

    from restscope.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.tools.context import ToolContext
    from restscope.tools.http import TargetHTTPRequestTool
    from restscope.target_http import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser

    coordinator, catalog = _resource_monitor(tmp_path)
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
        response_processor=APIBehaviorResponseProcessor(coordinator),
    )
    http_tool = TargetHTTPRequestTool(transport=transport)
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

    result = http_tool.execute(
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
    """Scenario: verify that resolved operation monitor error is persisted and later cleared."""
    from restscope.api_behavior_monitor.resource_identifiers.schemas import (
        MonitoredOperation,
        ResourceObservation,
    )
    from restscope.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.target_http import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )
    from restscope.openapi_parser import OpenAPIParser

    coordinator, catalog = _resource_monitor(tmp_path)
    coordinator.resource_identifier_tracker.observe(
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
    processor = APIBehaviorResponseProcessor(coordinator)
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
    """Scenario: verify that schema only dotted property fails closed without learning selector."""
    from restscope.api_behavior_monitor import (
        ResourceLookupRequest,
        APIBehaviorResponseProcessor,
    )
    from restscope.target_http import (
        TargetResponseObservation,
        TargetResponseOperationContext,
    )
    from restscope.openapi_parser import OpenAPIParser

    coordinator, catalog = _resource_monitor(tmp_path)
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
    processor = APIBehaviorResponseProcessor(coordinator)
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
    """Scenario: verify that response schema fields include collection item resource name."""
    from restscope.api_behavior_monitor.coordinator import _response_schema_fields
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


def test_default_app_uses_one_monitored_transport_without_global_model_tools(
    tmp_path: Path,
) -> None:
    """The App shares transport code but creates no global model tool box."""
    from restscope import RESTScopeApp
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DB_URL=sqlite:///{tmp_path / 'app.sqlite'}\n",
        encoding="utf-8",
    )
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
    )
    try:
        runtime = app.harness_runtime
        service = app.operation_testing_service
        assert service is not None
        raw_tool = runtime.target_http_tool

        assert app.api_behavior_monitor_coordinator is not None
        assert not hasattr(app.api_behavior_monitor_coordinator, "initialize")
        assert service.transport is raw_tool.transport
        assert service.transport.response_processor.coordinator is (
            app.api_behavior_monitor_coordinator
        )
        assert runtime.external_tools is None
    finally:
        app.close()
