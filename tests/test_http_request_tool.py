"""Regression scenarios for http request tool. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json

import pytest


def _toolbox_for_transport(
    transport,
    *,
    base_url="https://api.example.test/v1",
    secret_values=(),
):
    import httpx

    from restscope.observability import Redactor, TracingRuntime
    from restscope.openapi_parser import OpenAPIParser
    from restscope.tools import AgentToolbox, ToolFailure
    from restscope.tools.context import ToolContext
    from restscope.tools.http import (
        HTTPRequestTimeoutError,
        HTTPRequestToolError,
        TargetHTTPRequestTool,
        http_request_tool_spec,
    )

    http_tool = TargetHTTPRequestTool(
        client_factory=lambda **kwargs: httpx.Client(transport=transport, **kwargs),
    )
    toolbox = AgentToolbox(
        tracing_runtime=TracingRuntime.disabled(
            redactor=Redactor(secret_values),
        ),
    )
    context = ToolContext(
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

    def execute_http(**arguments):
        """Bind only the target context and expose expected transport errors."""
        try:
            return http_tool.execute(context, **arguments)
        except HTTPRequestTimeoutError as exc:
            raise ToolFailure(
                code=exc.code,
                message=str(exc),
                status="timed_out",
            ) from exc
        except HTTPRequestToolError as exc:
            raise ToolFailure(code=exc.code, message=str(exc)) from exc

    toolbox.register(spec=http_request_tool_spec(), execute=execute_http)
    return toolbox


def _execute(toolbox, **arguments):
    from restscope.llm import ToolCall

    return toolbox.execute(
        tool_call=ToolCall(
            id="http-request",
            name="restscope.http.request",
            arguments=arguments,
        )
    )


def test_harness_runtime_has_shared_http_implementation_without_global_tools() -> None:
    """The App reuses HTTP code without exposing an executable all-tools box."""
    from restscope.harness import build_harness

    runtime = build_harness()

    assert runtime.http_request_tool is not None
    assert not hasattr(runtime, "tool_registry")
    assert not hasattr(runtime, "tool_policy")
    assert not hasattr(runtime, "tool_selector")
    assert not hasattr(runtime, "tool_executor")


def test_http_request_tool_sends_target_json_and_preserves_full_response() -> None:
    """Scenario: verify that http request tool sends target json and preserves full response."""
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

    executor = _toolbox_for_transport(httpx.MockTransport(respond))

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


def test_http_tool_persists_only_results_that_match_an_openapi_operation() -> None:
    """Matched ordinary HTTP/transport results persist without Batch identity."""

    import httpx

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.response_processor import (
        APIBehaviorResponseProcessor,
    )
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext
    from restscope.tools.http import (
        HTTPRequestTimeoutError,
        TargetHTTPRequestTool,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(make_session_factory(engine))
    )

    class RecordingCatalog:
        """Remember writes while delegating the complete Catalog Interface."""

        def __init__(self) -> None:
            self.observations = []

        def __getattr__(self, name):
            return getattr(catalog, name)

        def record_observation(self, observation):
            record = catalog.record_observation(observation)
            self.observations.append(record)
            return record

    recording = RecordingCatalog()
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Ordinary HTTP", "version": "1"},
            "paths": {"/health": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={},
        base_url="https://api.example.test",
        headers={},
    )
    coordinator = APIBehaviorMonitorCoordinator(
        contract_tracker=ResponseContractTracker(recording),
        catalog=recording,
    )
    responses = iter(
        (
            httpx.Response(200, json={"healthy": True}),
            httpx.Response(404, text="unknown"),
        )
    )
    client = TargetAPIClient(
        response_processor=APIBehaviorResponseProcessor(coordinator),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda _request: next(responses)),
            **kwargs,
        ),
    )
    tool = TargetHTTPRequestTool(client=client)

    tool.execute(context, method="GET", path="/health")
    tool.execute(context, method="GET", path="/not-declared")

    assert len(recording.observations) == 1
    assert recording.observations[0].operation_id == "GET /health"
    assert recording.observations[0].batch_id is None

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("network detail", request=request)

    transport_tool = TargetHTTPRequestTool(
        client=TargetAPIClient(
            response_processor=APIBehaviorResponseProcessor(coordinator),
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(timeout),
                **kwargs,
            ),
        )
    )
    with pytest.raises(HTTPRequestTimeoutError):
        transport_tool.execute(context, method="GET", path="/health")

    assert len(recording.observations) == 2
    assert recording.observations[1].outcome_kind == "transport"
    assert recording.observations[1].batch_id is None


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
    """Scenario: verify that http request tool encodes text and form bodies."""
    import httpx

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.content == expected_content
        return httpx.Response(422, headers={"Content-Type": "text/plain"}, text="invalid request")

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(respond)),
        method="POST",
        path="/submit",
        **arguments,
    )

    assert result.status == "succeeded"
    assert result.structured["status_code"] == 422
    assert result.structured["body"] == "invalid request"
    assert result.structured["body_format"] == "text"


def test_http_request_tool_does_not_follow_redirects() -> None:
    """Scenario: verify that http request tool does not follow redirects."""
    import httpx

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://other.example.test/login"},
        )

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(respond)),
        method="GET",
        path="/redirect",
    )

    assert result.status == "succeeded"
    assert result.structured["status_code"] == 302
    assert result.structured["headers"]["location"] == "https://other.example.test/login"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_code"),
    [
        ("https://other.example.test/users", "denied", "invalid_tool_arguments"),
        ("//other.example.test/users", "denied", "invalid_tool_arguments"),
        ("/users?admin=true", "failed", "invalid_path"),
        ("/users#fragment", "failed", "invalid_path"),
        ("/safe/%252e%252e/secrets", "failed", "invalid_path"),
        ("/safe\\..\\secrets", "failed", "invalid_path"),
    ],
)
def test_http_request_tool_rejects_paths_that_can_escape_the_target(
    path,
    expected_status,
    expected_code,
) -> None:
    """Scenario: verify that http request tool rejects paths that can escape the target."""
    import httpx

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="GET",
        path=path,
    )

    assert result.status == expected_status
    assert result.error["code"] == expected_code


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
    """Scenario: verify that http request tool rejects sensitive and transport header overrides."""
    import httpx

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="GET",
        path="/users",
        headers={header: "attacker-value"},
    )

    assert result.status == "failed"
    assert result.error["code"] == "forbidden_header"
    assert "attacker-value" not in result.model_dump_json()


def test_http_request_tool_requires_one_request_body_encoding() -> None:
    """Scenario: verify that http request tool requires one request body encoding."""
    import httpx

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="POST",
        path="/users",
        json_body={"name": "Ada"},
        text_body="Ada",
    )

    assert result.status == "denied"
    assert result.error["code"] == "invalid_tool_arguments"


def test_http_request_tool_requires_a_configured_base_url() -> None:
    """Scenario: verify that http request tool requires a configured base url."""
    import httpx

    result = _execute(
        _toolbox_for_transport(
            httpx.MockTransport(lambda request: pytest.fail(str(request.url))),
            base_url=None,
        ),
        method="GET",
        path="/users",
    )

    assert result.status == "failed"
    assert result.error["code"] == "target_base_url_not_configured"


def test_http_request_tool_returns_stable_timeout_error() -> None:
    """Scenario: verify that http request tool returns stable timeout error."""
    import httpx

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout-secret", request=request)

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(timeout)),
        method="GET",
        path="/slow",
        timeout_seconds=0.1,
    )

    assert result.status == "timed_out"
    assert result.error == {"code": "request_timeout", "message": "HTTP request timed out"}
    assert "timeout-secret" not in result.model_dump_json()


def test_http_request_tool_returns_stable_network_error() -> None:
    """Scenario: verify that http request tool returns stable network error."""
    import httpx

    def disconnect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network-secret", request=request)

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(disconnect)),
        method="GET",
        path="/unavailable",
    )

    assert result.status == "failed"
    assert result.error == {
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
    """Scenario: verify that http request tool rejects unsafe response bodies."""
    import httpx

    result = _execute(
        _toolbox_for_transport(
            httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, content=content))
        ),
        method="GET",
        path="/download",
    )

    assert result.status == "failed"
    assert result.error["code"] == expected_code
    assert result.structured is None


def test_http_request_tool_preserves_complete_text_response() -> None:
    """Scenario: verify that http request tool preserves complete text response."""
    import httpx

    body = "prefix Bearer runtime-secret api_key=returned-secret suffix"
    result = _execute(
        _toolbox_for_transport(
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
    """Scenario: verify that http request tool preserves sensitive query values in result url."""
    import httpx

    result = _execute(
        _toolbox_for_transport(
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
    """Scenario: verify that http request tool only redacts registered app key."""
    import httpx

    app_key = "configured-llm-key"
    result = _execute(
        _toolbox_for_transport(
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
    """Scenario: verify that http request tool rejects binary without content type."""
    import httpx

    result = _execute(
        _toolbox_for_transport(
            httpx.MockTransport(lambda request: httpx.Response(200, content=b"\x00\x01"))
        ),
        method="GET",
        path="/binary",
    )

    assert result.status == "failed"
    assert result.error["code"] == "unsupported_response_media_type"


def test_http_request_tool_rejects_non_json_body_before_sending() -> None:
    """Scenario: verify that http request tool rejects non json body before sending."""
    import httpx

    result = _execute(
        _toolbox_for_transport(httpx.MockTransport(lambda request: pytest.fail(str(request.url)))),
        method="POST",
        path="/users",
        json_body={"payload": b"not-json"},
    )

    assert result.status == "failed"
    assert result.error["code"] == "invalid_request"
