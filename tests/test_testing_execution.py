"""Regression scenarios for testing execution. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from pathlib import Path


class _IdentityRedactor:
    def redact(self, value):
        return value


class _RecordingSpan:
    def set_output(self, value) -> None:
        pass

    def set_attribute(self, name, value) -> None:
        pass


class _RecordingTracingRuntime:
    def __init__(self) -> None:
        self.inputs: list[dict] = []
        self.redactor = _IdentityRedactor()

    def span(self, name, *, kind, input_value, attributes):
        from contextlib import contextmanager

        @contextmanager
        def opened():
            self.inputs.append(input_value)
            yield _RecordingSpan()

        return opened()


def _configured_catalog(tmp_path: Path, ir):
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.testing import GeneratorConfigCatalog

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'execution.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    return catalog


def _constrained_execution_setup(tmp_path: Path, *, tracing_runtime=None):
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Constrained execution", "version": "1"},
            "paths": {
                "/search": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "string",
                                    "enum": ["fast", "slow"],
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /search"]
    catalog = _configured_catalog(tmp_path, ir)
    requests: list[httpx.Request] = []
    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: requests.append(request)
                    or httpx.Response(200)
                ),
                **kwargs,
            )
        ),
        tracing_runtime=tracing_runtime,
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={
            "kind": "inline",
            "format": "json",
            "content": "{}",
        },
        base_url="https://api.example.test",
        headers={},
    )
    return operation, catalog, service, context, requests


def test_operation_testing_reads_only_failure_body_and_reports_unique_messages(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing reads only failure body and reports unique messages."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.execution import OperationTestingService

    class UnreadableBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("response body must not be read")

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Execution", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer", "minimum": 1},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /items/{itemId}"]
    catalog = _configured_catalog(tmp_path, ir)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = 200 if len(requests) == 1 else 503
        if status == 503:
            return httpx.Response(
                status,
                headers={"Content-Type": "application/json"},
                json={"message": "dependency unavailable"},
            )
        return httpx.Response(
            status,
            headers={"Content-Type": "application/json", "Content-Length": "999"},
            stream=UnreadableBody(),
        )

    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        )
    )
    service = OperationTestingService(config_catalog=catalog, transport=transport)
    context = ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test/v1",
        headers={"Authorization": "Bearer runtime-secret"},
    )

    outcome = service.run_operation_for_smoke(
        context,
        operation_key=operation.operation_key,
        case_count=2,
        seed=42,
    )
    report = outcome.report

    assert len(requests) == 2
    assert all(request.headers["Authorization"] == "Bearer runtime-secret" for request in requests)
    assert all(str(request.url).startswith("https://api.example.test/v1/items/") for request in requests)
    assert report.status == "completed"
    assert report.seed == 42
    assert report.config_revision == 1
    assert report.status_code_counts == {"200": 1, "503": 1}
    assert report.error_count == 0
    assert report.observed_2xx is True
    assert report.response_validation == "not_evaluated"
    assert [case.response.status_code for case in report.cases] == [200, 503]
    assert all(not hasattr(case.response, "body") for case in report.cases)
    assert report.failure_report.model_dump(mode="json") == {
        "unique_failure_messages": [
            {
                "failure_id": "f1",
                "message": "HTTP 503: dependency unavailable",
                "case_ids": [report.cases[1].case_id],
            }
        ],
        "truncated": False,
    }
    assert "runtime-secret" in report.model_dump_json()
    private = {
        item.case_id: item for item in outcome.case_evidence
    }
    assert private[report.cases[0].case_id].response_body is None
    assert private[report.cases[1].case_id].response_body == (
        b'{"message":"dependency unavailable"}'
    )
    assert not hasattr(report.cases[1], "response_body")


def test_operation_testing_executes_feedback_generator_outside_the_frozen_schema(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing executes feedback generator outside the frozen schema."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing.execution import OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Feedback execution", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "mode",
                                "in": "query",
                                "required": True,
                                "schema": {
                                    "type": "integer",
                                    "enum": [1, 2],
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /items"]
    catalog = _configured_catalog(tmp_path, ir)
    initial = catalog.inspect_operation(operation.operation_key)
    parameter_id = initial.snapshot.parameters[0].input_node_id
    patched = catalog.patch_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": parameter_id,
                "strategy": {
                    "type": "random_string",
                    "min_length": 8,
                    "max_length": 8,
                    "alphabet": "f",
                },
            }
        ],
    )
    assert patched.enabled is True
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(respond),
                **kwargs,
            )
        ),
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={},
    )

    report = service.run_operation(
        context,
        operation_key=operation.operation_key,
        seed=19,
    )

    assert len(requests) == 1
    assert requests[0].url.params["mode"] == "ffffffff"
    assert report.cases[0].generated_test_case.query_parameters == {
        "mode": "ffffffff"
    }


def test_smoke_execution_applies_constraints_and_traces_only_the_count(
    tmp_path: Path,
) -> None:
    """Scenario: verify that smoke execution applies constraints and traces only the count."""
    from restscope.testing import ConstraintSet

    tracing = _RecordingTracingRuntime()
    operation, catalog, service, context, requests = _constrained_execution_setup(
        tmp_path,
        tracing_runtime=tracing,
    )
    config = catalog.inspect_operation(operation.operation_key)
    mode_id = config.snapshot.parameters[0].input_node_id
    secret_literal = "slow"
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {
                    "type": "compare",
                    "operator": "==",
                    "left": {
                        "type": "input_value",
                        "input_node_id": mode_id,
                    },
                    "right": {
                        "type": "literal",
                        "value": secret_literal,
                    },
                }
            ]
        }
    )

    outcome = service.run_operation_for_smoke(
        context,
        operation_key=operation.operation_key,
        case_count=2,
        seed=17,
        constraints=constraints,
    )

    assert len(requests) == 2
    assert [request.url.params["mode"] for request in requests] == ["slow", "slow"]
    assert [
        case.generated_test_case.query_parameters["mode"]
        for case in outcome.report.cases
    ] == ["slow", "slow"]
    root_input = tracing.inputs[0]
    assert root_input["constraint_count"] == 1
    assert secret_literal not in str(root_input)


def test_constrained_smoke_preflight_failure_on_later_case_sends_no_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Scenario: verify that constrained smoke preflight failure on later case sends no requests."""
    import pytest

    from restscope.testing import ConstraintSet, TestingExecutionError
    from restscope.testing.constraint_solver import ConstraintSolveError
    from restscope.testing.generation import generate_test_case as real_generate

    operation, catalog, service, context, requests = _constrained_execution_setup(
        tmp_path
    )
    config = catalog.inspect_operation(operation.operation_key)
    mode_id = config.snapshot.parameters[0].input_node_id
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {"type": "present", "input_node_id": mode_id}
            ]
        }
    )

    def fail_second_case(*args, **kwargs):
        if kwargs["case_index"] == 1:
            raise ConstraintSolveError(
                "constraint_unsatisfiable",
                "second case has no solution",
                input_node_ids=(mode_id,),
            )
        return real_generate(*args, **kwargs)

    monkeypatch.setattr(
        "restscope.testing.execution.generate_test_case",
        fail_second_case,
    )

    with pytest.raises(TestingExecutionError) as raised:
        service.run_operation_for_smoke(
            context,
            operation_key=operation.operation_key,
            case_count=2,
            seed=17,
            constraints=constraints,
        )

    assert raised.value.code == "constraint_unsatisfiable"
    assert requests == []


def test_ordinary_operation_execution_does_not_accept_constraints(
    tmp_path: Path,
) -> None:
    """Scenario: verify that ordinary operation execution does not accept constraints."""
    import pytest

    from restscope.testing import ConstraintSet

    operation, catalog, service, context, requests = _constrained_execution_setup(
        tmp_path
    )
    mode_id = catalog.inspect_operation(
        operation.operation_key
    ).snapshot.parameters[0].input_node_id
    constraints = ConstraintSet.model_validate(
        {
            "constraints": [
                {"type": "present", "input_node_id": mode_id}
            ]
        }
    )

    with pytest.raises(TypeError):
        service.run_operation(
            context,
            operation_key=operation.operation_key,
            constraints=constraints,
        )

    assert requests == []


def test_operation_testing_preflight_failure_sends_no_requests(tmp_path: Path) -> None:
    """Scenario: verify that operation testing preflight failure sends no requests."""
    import httpx
    import pytest

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog, OperationTestingService
    from restscope.testing.serialization import SerializationError
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Preflight", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "filter",
                                "in": "query",
                                "required": True,
                                "style": "deepObject",
                                "explode": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"}
                                    },
                                },
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /items"]
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'preflight.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    initial = catalog.inspect_operation(operation.operation_key)
    parameter_id = initial.snapshot.parameters[0].input_node_id
    patched = catalog.patch_operation(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": parameter_id,
                "strategy": {
                    "type": "random_string",
                    "min_length": 4,
                    "max_length": 4,
                    "alphabet": "x",
                },
            }
        ],
    )
    assert patched.enabled is True
    requests = []

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(unexpected_request),
                **kwargs,
            )
        ),
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={},
    )

    with pytest.raises(
        SerializationError,
        match="deepObject query parameters require an exploded object",
    ):
        service.run_operation(context, operation_key=operation.operation_key, case_count=2, seed=2)

    assert requests == []


def test_operation_testing_isolates_cookies_and_reports_partial_transport_errors(tmp_path: Path) -> None:
    """Scenario: verify that operation testing isolates cookies and reports partial transport errors."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Isolation", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
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
    operation = ir.operations["GET /items/{itemId}"]
    catalog = _configured_catalog(tmp_path, ir)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "Cookie" not in request.headers
        if len(requests) == 1:
            return httpx.Response(200, headers={"Set-Cookie": "session=server-cookie"})
        raise httpx.ConnectError("not returned", request=request)

    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(respond),
                **kwargs,
            )
        ),
    )
    report = service.run_operation(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key=operation.operation_key,
        case_count=2,
        seed=9,
    )

    assert len(requests) == 2
    assert report.status == "partial"
    assert report.error_count == 1
    assert report.cases[1].transport_error.code == "request_failed"
    assert [
        item.message for item in report.failure_report.unique_failure_messages
    ] == [
        "TRANSPORT request_failed: HTTP request failed (ConnectError)",
    ]


def test_testing_transport_overrides_ordinary_context_headers_but_not_context_cookie() -> None:
    """Scenario: verify that testing transport overrides ordinary context headers but not context cookie."""
    import httpx

    from restscope.http_transport import TargetHTTPTransport

    seen: list[httpx.Request] = []
    transport = TargetHTTPTransport(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: seen.append(request) or httpx.Response(204)
            ),
            **kwargs,
        )
    )

    with transport.stream(
        method="POST",
        base_url="https://api.example.test",
        path="/submit",
        context_headers={
            "Content-Type": "text/plain",
            "Cookie": "session=context-cookie",
        },
        request_headers={
            "Content-Type": "application/json",
            "Cookie": "session=generated-cookie; case=generated-case",
        },
        override_context_headers=True,
        allowed_sensitive_request_headers={"cookie"},
        request_kwargs={"content": b"{}"},
    ):
        pass

    assert seen[0].headers["Content-Type"] == "application/json"
    assert seen[0].headers["Cookie"] == (
        "session=context-cookie; case=generated-case"
    )


def test_execution_report_preserves_sensitive_named_values_in_the_rendered_path(tmp_path: Path) -> None:
    """Scenario: verify that execution report preserves sensitive named values in the rendered path."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import (
        GeneratorConfigCatalog,
        InputGeneratorConfig,
        OperationTestingService,
    )

    secret = "ordinary-looking-path-secret"
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Sensitive path", "version": "1"},
            "paths": {
                "/users/{password}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "password",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /users/{password}"]
    node = next(iter(operation.input_nodes.values()))
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'redacted-path.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    catalog.patch_operation(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node.input_node_id,
                "strategy": {"type": "constant", "value": secret},
            }
        ],
    )
    sent_paths = []
    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: sent_paths.append(request.url.path) or httpx.Response(200)
                ),
                **kwargs,
            )
        ),
    )

    report = service.run_operation(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key=operation.operation_key,
        seed=1,
    )

    assert sent_paths == [f"/users/{secret}"]
    assert report.cases[0].request.path == f"/users/{secret}"
    assert secret in report.model_dump_json()


def test_transport_preflight_validates_every_case_before_the_first_request(tmp_path: Path) -> None:
    """Scenario: verify that transport preflight validates every case before the first request."""
    import httpx
    import pytest

    from restscope.capabilities import ToolContext
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.http_transport import TargetHTTPTransport, TargetHTTPTransportError
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import (
        GeneratorConfigCatalog,
        InputGeneratorConfig,
        OperationGeneratorConfig,
        OperationTestingService,
    )
    from restscope.testing.generation import generate_test_case

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Target preflight", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    operation = ir.operations["GET /items/{itemId}"]
    node = next(iter(operation.input_nodes.values()))
    input_config = InputGeneratorConfig(
        input_node_id=node.input_node_id,
        inclusion_probability=1,
        strategy={"type": "choice", "values": ["safe", ".."]},
    )
    from restscope.testing.snapshot import build_operation_snapshot

    snapshot, _ = build_operation_snapshot(operation)
    config = OperationGeneratorConfig(
        operation_key=operation.operation_key,
        revision=1,
        snapshot=snapshot,
        configs=[input_config],
    )
    seed = next(
        candidate
        for candidate in range(1000)
        if generate_test_case(snapshot, config, run_seed=candidate, case_index=0)
        .path_parameters["itemId"]
        == "safe"
        and generate_test_case(snapshot, config, run_seed=candidate, case_index=1)
        .path_parameters["itemId"]
        == ".."
    )
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'target-preflight.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    catalog.replace_operation(
        operation_key=operation.operation_key,
        expected_revision=1,
        active_media_type=None,
        configs=[input_config],
    )
    requests = []
    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: requests.append(request) or httpx.Response(200)
                ),
                **kwargs,
            )
        ),
    )

    with pytest.raises(TargetHTTPTransportError) as raised:
        service.run_operation(
            ToolContext(
                ir=ir,
                baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
                base_url="https://api.example.test",
                headers={},
            ),
            operation_key=operation.operation_key,
            case_count=2,
            seed=seed,
        )

    assert raised.value.code == "invalid_path"
    assert requests == []
