"""Protect generic Batch execution and its frozen generation revision."""

from __future__ import annotations


def test_batch_tool_returns_inline_cases_from_one_frozen_revision() -> None:
    """A Batch exposes no Test Case registry identity or persisted Failure."""
    import httpx

    from restscope.harness.operation_testing import OperationTestingService
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
        service=OperationTestingService(config_store=store, transport=transport),
        context_provider=lambda: context,
    )

    result = backend.run_batch(operation_key="GET /items", case_count=2, seed=9)["structured"]

    assert result["generation_revision"] == 0
    assert result["case_count"] == 2
    assert result["success_count"] == 2
    assert [case["case_number"] for case in result["cases"]] == [1, 2]
    assert all("limit=1" in url for url in sent)
    assert "run_id" not in result
    assert "case_id" not in str(result)


def test_batch_freezes_reference_values_with_generation_revision() -> None:
    """All cases use one pool snapshot even if the live provider changes later."""
    import httpx
    from contextlib import contextmanager

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
        SemanticParameterPatch,
    )
    from restscope.request_generation.store import ReferenceValueBinding
    from restscope.request_generation.reference_values import StagedReferenceUpdate
    from restscope.target_http import TargetHTTPTransport
    from restscope.tools.context import ToolContext

    class ResourceBackend:
        """Confirm one canonical resource without exposing other Tool behavior."""

        def list_ids(self, *, resource, limit):
            del limit
            return {
                "structured": {
                    "status": "found",
                    "canonical_resource": resource,
                }
            }

    class ChangingValues:
        """Return a different live pool each time so freezing is observable."""

        def __init__(self) -> None:
            self.pools = [[1]]
            self.calls = 0

        def values_for(self, _strategy):
            pool = self.pools[min(self.calls, len(self.pools) - 1)]
            self.calls += 1
            return pool

        @contextmanager
        def stage_updates(self, *, updates, **_arguments):
            strategy = updates[0].strategy
            yield StagedReferenceUpdate(
                updates=tuple(updates),
                bindings=(
                    ReferenceValueBinding(
                        input_node_id=updates[0].input_node_id,
                        kind="resource_identifier",
                        value_name=strategy.resource,
                    ),
                ),
                removed_response_value_inputs=(),
            )

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Frozen references", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
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
        resource_backend=ResourceBackend(),
    )
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "query.limit",
                    "inclusion_probability": 1,
                    "strategy": {
                        "type": "resource_identifier",
                        "resource": "limits",
                    },
                }
            ]
        }
    )
    validated = runtime.validate(
        operation_key="GET /items",
        expected_revision=0,
        affected_inputs=("query.limit",),
        patch=patch,
    )
    runtime.apply(
        operation_key="GET /items",
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("query.limit",),
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
        operation_key="GET /items",
        case_count=2,
        seed=5,
    )

    assert values.calls == 1
    assert all("limit=7" in url for url in sent)
