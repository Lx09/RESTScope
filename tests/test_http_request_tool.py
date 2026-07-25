from __future__ import annotations

import json

import pytest


def _executor_for_transport(
    transport,
    *,
    base_url="https://api.example.test/v1",
    secret_values=(),
):
    import httpx

    from restscope.capabilities import (
        ToolCallValidator,
        ToolContext,
        ToolExecutor,
        ToolPolicy,
        ToolRegistry,
        register_http_request_tool,
    )
    from restscope.observability import TracingRuntime
    from restscope.openapi_parser import OpenAPIParser
    from restscope.redaction import Redactor

    registry = ToolRegistry()
    register_http_request_tool(
        registry,
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    executor = ToolExecutor(
        registry,
        ToolCallValidator(registry, ToolPolicy()),
        tracing_runtime=TracingRuntime.disabled(
            redactor=Redactor(secret_values),
        ),
    )
    executor.bind_context(
        ToolContext(
            ir=OpenAPIParser.parse(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "HTTP Tool", "version": "1"},
                    "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
                }
            ),
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url=base_url,
            headers={
                "Authorization": "Bearer runtime-secret",
                "Accept": "application/problem+json",
            },
        )
    )
    return executor


def _execute(executor, **arguments):
    from restscope.llm import ToolCall

    return executor.execute(
        tool_call=ToolCall(
            id="http-request",
            name="restscope.http.request",
            arguments=arguments,
        ),
        role="future_agent",
        state={},
    )


def test_capability_runtime_registers_http_request_tool_for_every_role() -> None:
    from restscope.capabilities import build_capabilities

    runtime = build_capabilities()

    spec = runtime.tool_registry.get_spec("restscope.http.request")
    assert spec.kind == "local_function"
    assert spec.risk_level == "high"
    assert spec.read_only is False
    assert spec.requires_approval is False
    assert spec.timeout_seconds == 30
    assert spec.input_schema["required"] == ["method", "path"]

    for role in (
        "planner",
        "result_analyst",
        "operation_smoke_parameter_diagnosis",
        "decision_maker",
        "future_agent",
    ):
        selected = runtime.tool_selector.select_for_role(role=role, state={})
        assert spec in selected


def test_http_request_tool_sends_target_json_and_preserves_full_response() -> None:
    import httpx

    requests: list[httpx.Request] = []
    response_body = (
        b'{"id":1,"authorization":"response-secret",'
        b'"nested":[{"token":"response-token"}],'
        b'"x-api-key":"response-api-key",'
        b'"echo":"Bearer runtime-secret"}'
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            headers={
                "Authorization": "Bearer response-authorization",
                "Content-Type": "application/json",
                "Set-Cookie": "session=response-cookie",
                "WWW-Authenticate": "Bearer realm=target",
                "X-Request-ID": "request-1",
            },
            content=response_body,
        )

    executor = _executor_for_transport(httpx.MockTransport(respond))

    result = _execute(
        executor,
        method="POST",
        path="/users",
        query={"expand": ["roles", "groups"], "active": True},
        headers={"Accept": "application/json", "X-Trace": "trace-1"},
        json_body={"name": "Ada"},
    )

    assert result.status == "succeeded"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url.copy_with(query=None)) == "https://api.example.test/v1/users"
    assert request.url.params.get_list("expand") == ["roles", "groups"]
    assert request.url.params["active"] == "true"
    assert request.headers["Authorization"] == "Bearer runtime-secret"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["X-Trace"] == "trace-1"
    assert json.loads(request.content) == {"name": "Ada"}

    assert result.content == f"HTTP 201 POST /users ({len(response_body)} bytes)"
    assert result.structured == {
        "status_code": 201,
        "reason_phrase": "Created",
        "url": str(request.url),
        "headers": {
            "authorization": "Bearer response-authorization",
            "content-type": "application/json",
            "content-length": str(len(response_body)),
            "set-cookie": "session=response-cookie",
            "www-authenticate": "Bearer realm=target",
            "x-request-id": "request-1",
        },
        "body_format": "json",
        "body": {
            "id": 1,
            "authorization": "response-secret",
            "nested": [{"token": "response-token"}],
            "x-api-key": "response-api-key",
            "echo": "Bearer runtime-secret",
        },
        "size_bytes": len(response_body),
        "response_validation": "not_evaluated",
        "behavior_monitor_warnings": [],
    }
    assert "runtime-secret" in result.model_dump_json()
    assert "response-secret" in result.model_dump_json()
    assert "response-token" in result.model_dump_json()
    assert "response-api-key" in result.model_dump_json()
    assert "response-cookie" in result.model_dump_json()


@pytest.mark.parametrize(
    ("arguments", "expected_content"),
    [
        (
            {"text_body": "plain request"},
            b"plain request",
        ),
        (
            {"form_body": {"tag": ["one", "two"], "active": True}},
            b"tag=one&tag=two&active=true",
        ),
    ],
)
def test_http_request_tool_encodes_text_and_form_bodies(arguments, expected_content) -> None:
    import httpx

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.content == expected_content
        return httpx.Response(422, headers={"Content-Type": "text/plain"}, text="invalid request")

    result = _execute(
        _executor_for_transport(httpx.MockTransport(respond)),
        method="POST",
        path="/submit",
        **arguments,
    )

    assert result.status == "succeeded"
    assert result.structured["status_code"] == 422
    assert result.structured["body"] == "invalid request"
    assert result.structured["body_format"] == "text"


def test_http_request_tool_does_not_follow_redirects() -> None:
    import httpx

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://other.example.test/login"},
        )

    result = _execute(
        _executor_for_transport(httpx.MockTransport(respond)),
        method="GET",
        path="/redirect",
    )

    assert result.status == "succeeded"
    assert result.structured["status_code"] == 302
    assert result.structured["headers"]["location"] == "https://other.example.test/login"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "path",
    [
        "https://other.example.test/users",
        "//other.example.test/users",
        "/users?admin=true",
        "/users#fragment",
        "/safe/%252e%252e/secrets",
        "/safe\\..\\secrets",
    ],
)
def test_http_request_tool_rejects_paths_that_can_escape_the_target(path) -> None:
    import httpx

    result = _execute(
        _executor_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="GET",
        path=path,
    )

    assert result.status == "failed"
    assert result.error["code"] == "invalid_path"


@pytest.mark.parametrize(
    "header",
    [
        "Authorization",
        "Cookie",
        "Set-Cookie",
        "X-Auth",
        "X-API-Key",
        "X-Auth-Token",
        "X-Client-Secret",
        "Host",
        "Content-Length",
        "Transfer-Encoding",
    ],
)
def test_http_request_tool_rejects_sensitive_and_transport_header_overrides(header) -> None:
    import httpx

    result = _execute(
        _executor_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="GET",
        path="/users",
        headers={header: "attacker-value"},
    )

    assert result.status == "failed"
    assert result.error["code"] == "forbidden_header"
    assert "attacker-value" not in result.model_dump_json()


def test_http_request_tool_requires_one_request_body_encoding() -> None:
    import httpx

    result = _execute(
        _executor_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="POST",
        path="/users",
        json_body={"name": "Ada"},
        text_body="Ada",
    )

    assert result.status == "failed"
    assert result.error["code"] == "invalid_request"


def test_http_request_tool_requires_a_configured_base_url() -> None:
    import httpx

    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(lambda request: pytest.fail(str(request.url))),
            base_url=None,
        ),
        method="GET",
        path="/users",
    )

    assert result.status == "failed"
    assert result.error["code"] == "target_base_url_not_configured"


def test_http_request_tool_returns_stable_timeout_error() -> None:
    import httpx

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout-secret", request=request)

    result = _execute(
        _executor_for_transport(httpx.MockTransport(timeout)),
        method="GET",
        path="/slow",
        timeout_seconds=0.1,
    )

    assert result.status == "timed_out"
    assert result.error == {"code": "request_timeout", "message": "HTTP request timed out"}
    assert "timeout-secret" not in result.model_dump_json()


def test_http_request_tool_returns_stable_network_error() -> None:
    import httpx

    def disconnect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network-secret", request=request)

    result = _execute(
        _executor_for_transport(httpx.MockTransport(disconnect)),
        method="GET",
        path="/unavailable",
    )

    assert result.status == "failed"
    assert result.error == {
        "type": "HTTPRequestToolError",
        "message": "HTTP request failed (ConnectError)",
        "code": "request_failed",
    }
    assert "network-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("headers", "content", "expected_code"),
    [
        (
            {"Content-Type": "application/octet-stream"},
            b"\x00\x01\x02",
            "unsupported_response_media_type",
        ),
        (
            {"Content-Type": "text/plain"},
            b"x" * (10 * 1024 * 1024 + 1),
            "response_too_large",
        ),
    ],
)
def test_http_request_tool_rejects_unsafe_response_bodies(headers, content, expected_code) -> None:
    import httpx

    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, content=content))
        ),
        method="GET",
        path="/download",
    )

    assert result.status == "failed"
    assert result.error["code"] == expected_code
    assert result.structured is None


def test_http_request_tool_preserves_complete_text_response() -> None:
    import httpx

    body = "prefix Bearer runtime-secret api_key=returned-secret suffix"
    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/plain"},
                    text=body,
                )
            )
        ),
        method="GET",
        path="/text",
    )

    assert result.status == "succeeded"
    assert result.structured["size_bytes"] == len(body.encode())
    assert result.structured["body"] == body


def test_http_request_tool_preserves_sensitive_query_values_in_result_url() -> None:
    import httpx

    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "text/plain"},
                    text="ok",
                )
            )
        ),
        method="GET",
        path="/users",
        query={"password": "query-secret", "page": 2},
    )

    assert result.status == "succeeded"
    assert "password=query-secret" in result.structured["url"]
    assert "page=2" in result.structured["url"]


def test_http_request_tool_only_redacts_registered_app_key() -> None:
    import httpx

    app_key = "configured-llm-key"
    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": app_key,
                        "Set-Cookie": "session=visible-target-cookie",
                    },
                    json={
                        "token": "visible-generated-token",
                        "api_key": app_key,
                    },
                )
            ),
            secret_values=[app_key],
        ),
        method="GET",
        path="/users",
    )

    rendered = result.model_dump_json()
    assert result.status == "succeeded"
    assert app_key not in rendered
    assert result.structured["headers"]["x-api-key"] == "***REDACTED***"
    assert result.structured["headers"]["set-cookie"] == "session=visible-target-cookie"
    assert result.structured["body"] == {
        "token": "visible-generated-token",
        "api_key": "***REDACTED***",
    }


def test_http_request_tool_rejects_binary_without_content_type() -> None:
    import httpx

    result = _execute(
        _executor_for_transport(
            httpx.MockTransport(lambda request: httpx.Response(200, content=b"\x00\x01"))
        ),
        method="GET",
        path="/binary",
    )

    assert result.status == "failed"
    assert result.error["code"] == "unsupported_response_media_type"


def test_http_request_tool_rejects_non_json_body_before_sending() -> None:
    import httpx

    result = _execute(
        _executor_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="POST",
        path="/users",
        json_body={"payload": b"not-json"},
    )

    assert result.status == "failed"
    assert result.error["code"] == "invalid_request"
