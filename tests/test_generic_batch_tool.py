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
