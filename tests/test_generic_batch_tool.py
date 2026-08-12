"""Protect generic Batch execution and its frozen generation revision."""

from __future__ import annotations


def _response_monitor_catalog():
    """Create a real in-memory Catalog required before every Batch send."""

    from restscope.api_behavior_monitor.catalog import ResponseMonitorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyResponseMonitorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return ResponseMonitorCatalog(
        lambda: SqlAlchemyResponseMonitorUnitOfWork(sessions)
    )


def test_batch_tool_returns_inline_cases_from_one_frozen_revision() -> None:
    """A Batch exposes one durable abstract state identity, never per-case rows."""
    import httpx

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.api_behavior_monitor.catalog import ResponseMonitorCatalog
    from restscope.db import (
        Base,
        SqlAlchemyResponseMonitorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_http import TargetHTTPTransport
    from restscope.tools.context import ToolContext
    from restscope.tools.test_case import TestCaseBatchToolBackend

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Batch", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "integer", "enum": [1]},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = ResponseMonitorCatalog(
        lambda: SqlAlchemyResponseMonitorUnitOfWork(sessions)
    )
    sent: list[str] = []
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={},
    )
    backend = TestCaseBatchToolBackend(
        service=OperationTestingService(
            config_store=store,
            transport=transport,
            response_monitor_catalog=catalog,
        ),
        context_provider=lambda: context,
    )

    result = backend.run_batch(operation_key="GET /items", case_count=2, seed=9)["structured"]

    assert result["generation_revision"] == 0
    assert result["case_count"] == 2
    assert result["success_count"] == 2
    assert [case["case_number"] for case in result["cases"]] == [1, 2]
    assert all("limit=1" in url for url in sent)
    assert "run_id" not in result
    assert result["abstract_test_case_id"].startswith("abstract_test_case_")
    assert "case_number" in str(result)

    repeated = backend.run_batch(
        operation_key="GET /items",
        case_count=1,
        seed=10,
    )["structured"]
    assert repeated["abstract_test_case_id"] == result["abstract_test_case_id"]


def test_batch_freezes_reference_values_with_generation_revision() -> None:
    """All cases use one value snapshot even if live evidence changes later."""
    import httpx
    from contextlib import contextmanager

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
        SemanticParameterPatch,
    )
    from restscope.target_http import TargetHTTPTransport
    from restscope.tools.context import ToolContext

    class ChangingValues:
        """Return different live values each time so freezing is observable."""

        def __init__(self) -> None:
            self.pools = [[1]]
            self.calls = 0

        def values_for(self, _strategy):
            pool = self.pools[min(self.calls, len(self.pools) - 1)]
            self.calls += 1
            return pool

        def resource_key(self, _strategy):
            return "limits"

        def resource_records(self, _strategy):
            pool_index = max(0, min(self.calls - 1, len(self.pools) - 1))
            return tuple({"limit": value} for value in self.pools[pool_index])

        def resource_identity_fields(self, _strategy):
            return ("limit",)

        @contextmanager
        def stage_bindings(self, *, config, bindings):
            assert config.operation_key == "GET /items/{limit}"
            assert bindings[0].resource_name == "limits"
            yield

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Frozen references", "version": "1"},
            "paths": {
                "/items/{limit}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    values = ChangingValues()
    runtime = RequestGenerationPatchRuntime(
        store=store,
        ir_provider=lambda: ir,
        reference_values=values,
        reference_binding_stager=values,
    )
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "path.limit",
                    "inclusion_probability": 1,
                    "strategy": {
                        "type": "resource_identifier",
                        "source": {
                            "operation_key": "GET /limits",
                            "status_code": 200,
                            "media_type": "application/json",
                            "field": "body.items[].limit",
                        },
                    },
                }
            ]
        }
    )
    validated = runtime.validate(
        operation_key="GET /items/{limit}",
        expected_revision=0,
        affected_inputs=("path.limit",),
        patch=patch,
    )
    runtime.apply(
        operation_key="GET /items/{limit}",
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("path.limit",),
        patch=patch,
    )

    values.pools = [[7], [8]]
    values.calls = 0
    sent: list[str] = []
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    service = OperationTestingService(
        config_store=store,
        response_monitor_catalog=_response_monitor_catalog(),
        transport=transport,
        reference_values=values,
    )
    service.run_batch(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key="GET /items/{limit}",
        case_count=2,
        seed=5,
    )

    assert values.calls == 1
    assert all(url.endswith("/items/7") for url in sent)


def test_abstract_case_persistence_failure_stops_batch_before_network() -> None:
    """No target request escapes when the mandatory audit snapshot cannot commit."""

    import httpx
    import pytest

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_http import TargetHTTPTransport
    from restscope.tools.context import ToolContext

    class FailingCatalog:
        """Accept operation metadata, then fail the abstract snapshot write."""

        def ensure_operation(self, operation):
            return operation

        def ensure_abstract_test_case(self, _test_case):
            raise RuntimeError("abstract case storage failed")

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Preflight", "version": "1"},
            "paths": {
                "/items": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    sent: list[str] = []
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    service = OperationTestingService(
        config_store=store,
        response_monitor_catalog=FailingCatalog(),
        transport=transport,
    )

    with pytest.raises(RuntimeError, match="abstract case storage failed"):
        service.run_batch(
            ToolContext(
                ir=ir,
                baseline_schema_source={},
                base_url="https://api.example.test",
                headers={},
            ),
            operation_key="GET /items",
            case_count=1,
            seed=3,
        )

    assert sent == []
