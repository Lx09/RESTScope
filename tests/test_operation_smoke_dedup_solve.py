"""Cross-role request and HTTP-scope contracts for the simplified lifecycle."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from restscope.operation_smoke.failure_solver import CurrentOperationHTTPProbe
from restscope.operation_smoke import OperationSmokeRequest
from restscope.llm import ToolCall
from tests._operation_smoke_dedup_solve_fixtures import smoke_config


def _validation_only_probe() -> CurrentOperationHTTPProbe:
    """Build a Probe whose explicit dependencies cannot run during validation."""
    from restscope.capabilities import TargetHTTPRequestTool

    return CurrentOperationHTTPProbe(
        http_tool=TargetHTTPRequestTool(),
        context_provider=lambda: (_ for _ in ()).throw(
            AssertionError("validation must not request App context")
        ),
    )


def test_operation_smoke_request_uses_large_output_budgets() -> None:
    """The public request exposes the new role budgets and no legacy controls."""
    request = OperationSmokeRequest(
        operation_key="GET /projects/{projectId}"
    )

    assert request.max_dedup_outputs == 50
    assert request.max_solve_outputs_per_todo == 50
    assert request.max_patch_outputs == 20
    assert not hasattr(request, "continuation_interval")
    assert not hasattr(request, "seed")
    assert not hasattr(request, "max_effect_outputs")
    assert not hasattr(request, "max_feedback_rounds")
    assert not hasattr(request, "max_diagnosis_outputs_per_failure")
    assert not hasattr(request, "max_patch_attempts")


@pytest.mark.parametrize(
    "legacy_field",
    [
        "max_feedback_rounds",
        "max_diagnosis_outputs_per_failure",
        "max_patch_attempts",
        "seed",
        "max_effect_outputs",
        "continuation_interval",
    ],
)
def test_operation_smoke_request_rejects_legacy_budget_fields(
    legacy_field: str,
) -> None:
    """No compatibility layer silently revives the old state-machine budget."""
    with pytest.raises(ValidationError):
        OperationSmokeRequest.model_validate(
            {
                "operation_key": "GET /projects/{projectId}",
                legacy_field: 3,
            }
        )


def test_failure_solve_http_probe_rejects_cross_operation_method_and_path() -> None:
    """Solve may vary parameters but cannot leave the current method/template."""
    probe = _validation_only_probe()

    wrong_method = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="wrong-method",
            name="restscope.http.request",
            arguments={"method": "POST", "path": "/projects/known"},
        ),
    )
    wrong_path = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="wrong-path",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/users/known"},
        ),
    )
    allowed = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="allowed",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/projects/known"},
        ),
    )

    assert "method must be GET" in wrong_method
    assert "must match" in wrong_path
    assert allowed is None


def test_failure_solve_http_probe_atomically_preflights_strict_arguments() -> None:
    """Unknown HTTP fields are rejected before Failure Solve executes a call."""
    probe = _validation_only_probe()

    error = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="invalid-arguments",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/known",
                "unexpected": True,
            },
        ),
    )

    assert error is not None
    assert "unexpected" in error


def test_failure_solve_http_probe_allows_the_exact_mutating_operation() -> None:
    """Solve may investigate its current DELETE operation without crossing scope."""
    probe = _validation_only_probe()
    config = smoke_config()
    config = config.model_copy(
        update={
            "operation_key": "DELETE /projects/{projectId}",
            "snapshot": config.snapshot.model_copy(
                update={
                    "operation_key": "DELETE /projects/{projectId}",
                    "method": "DELETE",
                }
            ),
        }
    )

    error = probe.validate(
        config=config,
        tool_call=ToolCall(
            id="forged-delete",
            name="restscope.http.request",
            arguments={"method": "DELETE", "path": "/projects/known"},
        ),
    )

    assert error is None


def test_failure_solve_probe_records_a_new_catalog_case_without_returning_body() -> None:
    """An attempted probe becomes immediately queryable through its new TC ref."""
    import httpx

    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.operation_smoke.test_case_catalog import TestCaseCatalog
    from restscope.testing import build_semantic_input_map

    config = smoke_config()
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Probe", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "projectId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"404": {"description": "missing"}},
                    }
                }
            },
        }
    )
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    404,
                    headers={"Content-Type": "application/json"},
                    json={"message": "project missing"},
                )
            ),
            **kwargs,
        )
    )
    runtime = build_capabilities(target_http_transport=transport)
    runtime.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={},
            base_url="https://api.example.test",
        )
    )
    catalog = TestCaseCatalog(
        input_references=build_semantic_input_map(config).reference_by_handle.values()
    )

    result = CurrentOperationHTTPProbe(
        http_tool=runtime.target_http_tool,
        context_provider=runtime.require_context,
    ).execute(
        config=config,
        tool_call=ToolCall(
            id="probe-1",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/known",
            },
        ),
        catalog=catalog,
    )

    assert result.status == "succeeded", result
    assert result.structured == {
        "case_id": "TC1",
        "status_code": 404,
        "failure": {
            "kind": "http",
            "status_code": 404,
            "messages": ["HTTP 404: project missing"],
            "body_truncated": False,
        },
    }
    assert "body" not in result.structured
    assert catalog.get_parameter_value(
        case_ids=["TC1"],
        parameter="path.projectId",
    )["cases"]["TC1"]["request"] == {
        "path": {"projectId": "known"}
    }
    assert catalog.get_response_field_value(
        case_ids=["TC1"],
        field="body.message",
    )["cases"]["TC1"]["response"] == {
        "body": {"message": "project missing"}
    }


def test_probe_normalizes_transport_arguments_to_direct_name_request_json() -> None:
    """Probe evidence uses direct names and excludes runtime authentication."""
    import httpx

    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.capabilities import operation_input_references
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.operation_smoke.test_case_catalog import TestCaseCatalog
    from restscope.testing.snapshot import build_initial_operation_config

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Probe JSON", "version": "1"},
            "paths": {
                "/items/{id}": {
                    "post": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "sort",
                                "in": "query",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "sort",
                                "in": "header",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "mode",
                                "in": "cookie",
                                "schema": {"type": "string"},
                            },
                        ],
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
    operation = ir.operations["POST /items/{id}"]
    config = build_initial_operation_config(operation)
    sent_cookie_headers: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        """Capture the wire Cookie while returning a deterministic failure."""
        sent_cookie_headers.append(request.headers["cookie"])
        return httpx.Response(
            400,
            headers={"Content-Type": "application/json"},
            json={"message": "bad"},
        )

    runtime = build_capabilities(
        target_http_transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(respond),
                **kwargs,
            )
        )
    )
    runtime.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={},
            base_url="https://api.example.test",
            headers={"Cookie": "session=runtime"},
        )
    )
    catalog = TestCaseCatalog(
        input_references=operation_input_references(operation)
    )

    result = CurrentOperationHTTPProbe(
        http_tool=runtime.target_http_tool,
        context_provider=runtime.require_context,
    ).execute(
        config=config,
        tool_call=ToolCall(
            id="probe-json",
            name="restscope.http.request",
            arguments={
                "method": "POST",
                "path": "/items/7",
                "query": {"sort": "query-value"},
                "headers": {
                    "sort": "header-value",
                    "Cookie": "mode=compact",
                },
                "json_body": {"name": "demo"},
            },
        ),
        catalog=catalog,
    )

    assert result.status == "succeeded"
    case = catalog.get_case("TC1")
    assert case.request == {
        "path": {"id": 7},
        "query": {"sort": "query-value"},
        "header": {"sort": "header-value"},
        "cookie": {"mode": "compact"},
        "body": {"name": "demo"},
    }
    assert len(sent_cookie_headers) == 1
    assert {
        part.strip()
        for part in sent_cookie_headers[0].split(";")
    } == {"mode=compact", "session=runtime"}
    assert catalog.get_parameter_value(
        case_ids=["TC1"], parameter="query.sort"
    )["cases"]["TC1"]["request"] == {
        "query": {"sort": "query-value"}
    }
    assert catalog.get_parameter_value(
        case_ids=["TC1"], parameter="header.sort"
    )["cases"]["TC1"]["request"] == {
        "header": {"sort": "header-value"}
    }
