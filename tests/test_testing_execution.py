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
    from restscope.harness.testing import GeneratorConfigCatalog

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'execution.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    return catalog


def _accept_patch(catalog, operation_key: str, updates):
    """Write test setup through the repository's current-content compare seam."""
    from restscope.harness.testing import prepare_accepted_generator_patch

    current = catalog.require_operation(operation_key)
    updated = prepare_accepted_generator_patch(current, updates)
    with catalog.unit_of_work_factory() as uow:
        uow.generator_configs.replace_inputs(
            operation_key=operation_key,
            expected=current.configs,
            updated=updated.configs,
        )
        uow.commit()
    return catalog.require_operation(operation_key)


def _constrained_execution_setup(tmp_path: Path, *, tracing_runtime=None):
    import httpx

    from restscope.tools import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import OperationTestingService

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


def test_smoke_execution_projects_all_real_cases_into_one_semantic_batch(
    tmp_path: Path,
) -> None:
    """Scenario: the production Batch path emits one card with every final request."""
    from restscope.observability import LiveRunObserver, TracingRuntime

    observer = LiveRunObserver()
    observer.begin_run({})
    tracing = TracingRuntime(run_observer=observer)
    operation, _catalog, service, context, requests = _constrained_execution_setup(
        tmp_path,
        tracing_runtime=tracing,
    )
    # App construction normally binds the same observer to both tracing and
    # target transport. This focused setup mirrors that ownership explicitly.
    service.transport.run_observer = observer

    result = service.run_smoke_batch(
        context,
        operation_key=operation.operation_key,
        case_count=2,
        seed=17,
    )
    snapshot = observer.snapshot()

    assert len(requests) == 2
    assert len(snapshot["events"]) == 1
    batch = snapshot["events"][0]
    assert batch["kind"] == "smoke_batch"
    assert batch["detail"]["run_id"] == result.run_id
    assert batch["detail"]["seed"] == 17
    assert batch["detail"]["success_count"] == 2
    assert [case["case_id"] for case in batch["detail"]["cases"]] == ["TC1", "TC2"]
    assert [case["request"]["url"] for case in batch["detail"]["cases"]] == [
        str(request.url) for request in requests
    ]
    assert all(case["response"]["body_retained"] for case in batch["detail"]["cases"])


def test_operation_testing_returns_catalog_cases_and_only_keeps_failure_body(
    tmp_path: Path,
) -> None:
    """Scenario: Batch returns Catalog-ready cases without a parallel report."""
    import httpx

    from restscope.tools import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing.execution import (
        OperationTestingService,
        SMOKE_FAILURE_RESPONSE_BYTES,
    )

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
        status = [200, 503, 302][len(requests) - 1]
        if status == 503:
            return httpx.Response(
                status,
                headers={"Content-Type": "application/json"},
                json={"message": "dependency unavailable"},
            )
        if status == 302:
            return httpx.Response(
                status,
                headers={"Content-Type": "application/json"},
                json={"message": "redirect body is not retained"},
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

    batch = service.run_smoke_batch(
        context,
        operation_key=operation.operation_key,
        case_count=3,
        seed=42,
    )

    assert SMOKE_FAILURE_RESPONSE_BYTES == 10 * 1024 * 1024
    assert len(requests) == 3
    assert all(request.headers["Authorization"] == "Bearer runtime-secret" for request in requests)
    assert all(str(request.url).startswith("https://api.example.test/v1/items/") for request in requests)
    assert batch.seed == 42
    assert not hasattr(batch, "config_revision")
    assert batch.operation_key == operation.operation_key
    assert [case.case_id for case in batch.cases] == ["TC1", "TC2", "TC3"]
    assert all(case.request["path"]["itemId"] >= 1 for case in batch.cases)

    # A success still remains queryable by input value, but its potentially
    # large response body is deliberately not retained.
    assert batch.cases[0].response_body is None
    assert batch.cases[0].failure is None

    # A 4xx/5xx response keeps both the complete decoded body and a separately
    # normalized Failure so Agents can ask for either fact.
    assert batch.cases[1].response_body == {
        "message": "dependency unavailable"
    }
    assert batch.cases[1].failure is not None
    assert batch.cases[1].failure.model_dump() == {
        "kind": "http",
        "status_code": 503,
        "messages": ["HTTP 503: dependency unavailable"],
        "body_truncated": False,
    }
    # Redirects remain failed Test Cases but do not consume response-body
    # storage intended only for actionable 4xx/5xx evidence.
    assert batch.cases[2].response_body is None
    assert batch.cases[2].failure is not None
    assert batch.cases[2].failure.messages == ["HTTP 302 Found"]
    assert not hasattr(batch, "report")


def test_operation_testing_executes_feedback_generator_outside_the_frozen_schema(
    tmp_path: Path,
) -> None:
    """Scenario: verify that operation testing executes feedback generator outside the frozen schema."""
    import httpx

    from restscope.tools import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing.execution import OperationTestingService

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
    initial = catalog.require_operation(operation.operation_key)
    parameter_id = initial.snapshot.parameters[0].input_node_id
    patched = _accept_patch(
        catalog,
        initial.operation_key,
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

    batch = service.run_smoke_batch(
        context,
        operation_key=operation.operation_key,
        seed=19,
    )

    assert len(requests) == 1
    assert requests[0].url.params["mode"] == "ffffffff"
    assert batch.cases[0].request["query"]["mode"] == "ffffffff"


def test_smoke_execution_applies_constraints_and_traces_only_the_count(
    tmp_path: Path,
) -> None:
    """Scenario: verify that smoke execution applies constraints and traces only the count."""
    from restscope.harness.testing import ConstraintSet

    tracing = _RecordingTracingRuntime()
    operation, catalog, service, context, requests = _constrained_execution_setup(
        tmp_path,
        tracing_runtime=tracing,
    )
    config = catalog.require_operation(operation.operation_key)
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

    batch = service.run_smoke_batch(
        context,
        operation_key=operation.operation_key,
        case_count=2,
        seed=17,
        constraints=constraints,
    )

    assert len(requests) == 2
    assert [request.url.params["mode"] for request in requests] == ["slow", "slow"]
    assert [
        case.request["query"]["mode"]
        for case in batch.cases
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

    from restscope.harness.testing import ConstraintSet, TestingExecutionError
    from restscope.harness.testing.constraint_solver import ConstraintSolveError
    from restscope.harness.testing.generation import generate_test_case as real_generate

    operation, catalog, service, context, requests = _constrained_execution_setup(
        tmp_path
    )
    config = catalog.require_operation(operation.operation_key)
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
        "restscope.harness.testing.execution.generate_test_case",
        fail_second_case,
    )

    with pytest.raises(TestingExecutionError) as raised:
        service.run_smoke_batch(
            context,
            operation_key=operation.operation_key,
            case_count=2,
            seed=17,
            constraints=constraints,
        )

    assert raised.value.code == "constraint_unsatisfiable"
    assert requests == []


def test_operation_testing_preflight_failure_sends_no_requests(tmp_path: Path) -> None:
    """Scenario: verify that operation testing preflight failure sends no requests."""
    import httpx
    import pytest

    from restscope.tools import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import GeneratorConfigCatalog, OperationTestingService
    from restscope.harness.testing.serialization import SerializationError
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
    initial = catalog.require_operation(operation.operation_key)
    parameter_id = initial.snapshot.parameters[0].input_node_id
    patched = _accept_patch(
        catalog,
        initial.operation_key,
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
        service.run_smoke_batch(
            context,
            operation_key=operation.operation_key,
            case_count=2,
            seed=2,
        )

    assert requests == []


def test_operation_testing_isolates_cookies_and_reports_partial_transport_errors(tmp_path: Path) -> None:
    """Scenario: verify that operation testing isolates cookies and reports partial transport errors."""
    import httpx

    from restscope.tools import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import OperationTestingService

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
    batch = service.run_smoke_batch(
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
    assert batch.success_count == 1
    assert batch.cases[1].failure is not None
    assert batch.cases[1].failure.kind == "transport"
    assert batch.cases[1].failure.code == "request_failed"
    assert batch.cases[1].failure.messages[0].startswith(
        "TRANSPORT request_failed: HTTP request failed"
    )


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


def test_batch_preserves_sensitive_named_values_only_as_catalog_parameters(tmp_path: Path) -> None:
    """A sent path value remains queryable without reviving request reports."""
    import httpx

    from restscope.tools import ToolContext
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import (
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
    _accept_patch(
        catalog,
        operation.operation_key,
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

    batch = service.run_smoke_batch(
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
    assert batch.cases[0].request["path"]["password"] == secret
    assert not hasattr(batch.cases[0], "parameters")


def test_transport_preflight_validates_every_case_before_the_first_request(tmp_path: Path) -> None:
    """Scenario: verify that transport preflight validates every case before the first request."""
    import httpx
    import pytest

    from restscope.tools import ToolContext
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.http_transport import TargetHTTPTransport, TargetHTTPTransportError
    from restscope.openapi_parser import OpenAPIParser
    from restscope.harness.testing import (
        GeneratorConfigCatalog,
        InputGeneratorConfig,
        OperationGeneratorConfig,
        OperationTestingService,
    )
    from restscope.harness.testing.generation import generate_test_case

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
    from restscope.harness.testing.snapshot import build_operation_snapshot

    snapshot, _ = build_operation_snapshot(operation)
    config = OperationGeneratorConfig(
        operation_key=operation.operation_key,
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
    _accept_patch(
        catalog,
        operation.operation_key,
        updates=[
            {
                "input_node_id": input_config.input_node_id,
                "inclusion_probability": input_config.inclusion_probability,
                "strategy": input_config.strategy,
            }
        ],
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
        service.run_smoke_batch(
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
