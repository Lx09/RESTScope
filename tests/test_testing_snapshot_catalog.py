"""Regression scenarios for testing snapshot catalog. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from pathlib import Path


def _spec(*, path: str = "/orders/{orderId}", title: str = "Initial") -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1"},
        "paths": {
            path: {
                "post": {
                    "parameters": [
                        {
                            "name": "orderId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 10},
                        },
                        {
                            "name": "verbose",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "responses": {"204": {"description": "ok"}},
                }
            }
        },
    }


def _catalog(tmp_path: Path):
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.testing import GeneratorConfigCatalog

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'snapshot.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
    )


def test_catalog_initializes_all_operation_generators_once_from_ir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Scenario: verify that catalog initializes all operation generators once from ir."""
    from restscope.openapi_parser import OpenAPIParser

    catalog = _catalog(tmp_path)
    initial_ir = OpenAPIParser.parse(_spec())

    assert catalog.initialize_once(initial_ir) is True
    stored = catalog.get_operation("POST /orders/{orderId}")

    assert stored is not None
    assert stored.revision == 1
    assert stored.enabled is True
    assert stored.snapshot.operation_key == "POST /orders/{orderId}"
    assert stored.snapshot.path == "/orders/{orderId}"
    path_by_id = {
        item.input_node_id: item.canonical_path
        for item in stored.snapshot.input_nodes
    }
    assert {
        path_by_id[item.input_node_id]: item.inclusion_probability
        for item in stored.configs
    } == {
        "path/orderId": 1.0,
        "query/verbose": 0.5,
    }
    assert all(
        not hasattr(item, "expected_node_fingerprint")
        for item in stored.configs
    )

    changed_ir = OpenAPIParser.parse(_spec(path="/replacement", title="Changed"))
    monkeypatch.setattr(
        "restscope.testing.catalog.build_initial_catalog",
        lambda _ir: (_ for _ in ()).throw(
            AssertionError("initialized catalog must not inspect a later IR")
        ),
    )
    assert catalog.initialize_once(changed_ir) is False
    assert catalog.get_operation("POST /orders/{orderId}") == stored
    assert catalog.get_operation("POST /replacement") is None


def test_app_initialize_creates_catalog_before_binding_context(tmp_path: Path) -> None:
    """Scenario: verify that app initialize creates catalog before binding context."""
    import json

    from restscope import RESTScopeApp
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    database = tmp_path / "app.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DATA_DIR={tmp_path / 'data'}\nDB_URL=sqlite:///{database}\n",
        encoding="utf-8",
    )
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
    )
    try:
        app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(_spec()),
            },
            base_url="https://api.example.test",
        )

        service = app.capability_runtime.operation_testing_service
        assert service is not None
        assert service.config_catalog.get_operation("POST /orders/{orderId}") is not None
    finally:
        app.close()


def test_second_app_start_is_rejected_after_first_catalog_is_initialized(
    tmp_path: Path,
) -> None:
    """Scenario: verify that second app start is rejected after first catalog is initialized."""
    import json

    import pytest

    from restscope import RESTScopeApp
    from restscope.db import DatabaseAlreadyExistsError
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    database = tmp_path / "shared-app.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(f"DB_URL=sqlite:///{database}\n", encoding="utf-8")
    config = RESTScopeConfig.from_environment(env_file)

    def build_app():
        return RESTScopeApp.from_config(
            config,
            operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        )

    first = build_app()
    first.initialize(
        schema_source={
            "kind": "inline",
            "format": "json",
            "content": json.dumps(_spec()),
        }
    )
    first.close()

    with pytest.raises(DatabaseAlreadyExistsError) as exc_info:
        build_app()
    assert exc_info.value.code == "database_already_exists"


def test_smoke_batch_uses_persisted_snapshot_when_current_ir_is_different(
    tmp_path: Path,
) -> None:
    """Scenario: Smoke uses the persisted snapshot even when the current IR differs."""
    import httpx

    from restscope.capabilities import ToolContext
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import OperationTestingService

    catalog = _catalog(tmp_path)
    catalog.initialize_once(OpenAPIParser.parse(_spec()))
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(204)

    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )
        ),
    )
    current_ir = OpenAPIParser.parse(_spec(path="/replacement", title="Changed"))
    batch = service.run_smoke_batch(
        ToolContext(
            ir=current_ir,
            baseline_schema_source={
                "kind": "inline",
                "format": "json",
                "content": _spec(path="/replacement"),
            },
            base_url="https://api.example.test/v1",
            headers={},
        ),
        operation_key="POST /orders/{orderId}",
        case_count=1,
        seed=3,
    )

    assert batch.success_count == 1
    assert len(requested_urls) == 1
    assert requested_urls[0].startswith("https://api.example.test/v1/orders/")
    assert "/replacement" not in requested_urls[0]


def test_catalog_patches_modify_frozen_generators_by_accepted_revision(
    tmp_path: Path,
) -> None:
    """Directly accepted Patches preserve the snapshot and revision lock."""
    import pytest

    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import (
        GeneratorConfigRevisionConflict,
        InputGeneratorPatch,
    )

    catalog = _catalog(tmp_path)
    catalog.initialize_once(OpenAPIParser.parse(_spec()))
    initial = catalog.get_operation("POST /orders/{orderId}")
    assert initial is not None
    verbose = next(
        item
        for item in initial.configs
        if next(
            node
            for node in initial.snapshot.input_nodes
            if node.input_node_id == item.input_node_id
        ).canonical_path
        == "query/verbose"
    )

    patched = catalog.apply_accepted_patch(
        operation_key=initial.operation_key,
        expected_revision=1,
        updates=[
            InputGeneratorPatch(
                input_node_id=verbose.input_node_id,
                inclusion_probability=1,
                strategy={"type": "constant", "value": True},
            )
        ],
    )

    assert patched.revision == 2
    assert patched.snapshot == initial.snapshot
    updated_verbose = next(
        item
        for item in patched.configs
        if item.input_node_id == verbose.input_node_id
    )
    assert updated_verbose.inclusion_probability == 1
    assert updated_verbose.strategy.type == "constant"
    assert updated_verbose.strategy.value is True

    path_node_id = next(
        node.input_node_id
        for node in patched.snapshot.input_nodes
        if node.canonical_path == "path/orderId"
    )
    replaced = catalog.apply_accepted_patch(
        operation_key=patched.operation_key,
        expected_revision=2,
        updates=[
            InputGeneratorPatch(
                input_node_id=path_node_id,
                strategy={"type": "constant", "value": 5},
            )
        ],
    )

    assert replaced.revision == 3
    assert replaced.snapshot == initial.snapshot
    with pytest.raises(GeneratorConfigRevisionConflict):
        catalog.apply_accepted_patch(
            operation_key=initial.operation_key,
            expected_revision=2,
            updates=[
                InputGeneratorPatch(
                    input_node_id=verbose.input_node_id,
                    inclusion_probability=0,
                )
            ],
        )


def test_generator_patch_requires_an_actual_change() -> None:
    """Scenario: verify that generator patch requires an actual change."""
    import pytest
    from pydantic import ValidationError

    from restscope.testing import InputGeneratorPatch

    with pytest.raises(ValidationError):
        InputGeneratorPatch(input_node_id="input_example")
