from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest


def _config(tmp_path: Path):
    from restscope.db import Base, create_engine_from_config
    from restscope.restscope_config import DBConfig, RESTScopeConfig

    config = RESTScopeConfig.from_environment()
    config = replace(config, db=DBConfig(url=f"sqlite:///{tmp_path / 'catalog.sqlite'}"))
    Base.metadata.create_all(create_engine_from_config(config.db))
    return config


def _petstore_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Petstore", "version": "1.0.0"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "summary": "Create a pet",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"id": {"type": "string"}},
                                    }
                                }
                            },
                        }
                    },
                }
            },
            "/pets/{id}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "responses": {"200": {"description": "found"}},
                }
            },
        },
    }


def test_initialize_openapi_catalog_persists_complete_ready_catalog(tmp_path: Path) -> None:
    from restscope.catalog import OpenAPIInitializationRequest, initialize_openapi_catalog
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    result = initialize_openapi_catalog(
        config,
        OpenAPIInitializationRequest(source=_petstore_spec(), name="Petstore", version="1.0.0"),
    )

    assert result.status == "ready"
    assert result.operation_count == 2

    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        schema = uow.schemas.require(result.schema_id)
        operations = uow.operations.list_by_schema(result.schema_id)
        edges = uow.operation_edges.list_by_schema(result.schema_id)
        intelligence = [uow.intelligence.get(operation.id) for operation in operations]

    assert schema.catalog_status == "ready"
    assert schema.normalized_spec_json == _petstore_spec()
    assert schema.parser_version
    assert schema.initialized_at is not None
    assert len(operations) == 2
    assert all(operation.card_json for operation in operations)
    assert all(item is not None for item in intelligence)
    assert any(edge.source_operation_id != edge.target_operation_id for edge in edges)


def test_second_initialization_is_rejected_before_source_loading(tmp_path: Path) -> None:
    from restscope.catalog import (
        CatalogInitializationError,
        OpenAPIInitializationRequest,
        initialize_openapi_catalog,
    )

    config = _config(tmp_path)
    initialize_openapi_catalog(
        config,
        OpenAPIInitializationRequest(source=_petstore_spec(), name="Petstore"),
    )

    with pytest.raises(CatalogInitializationError) as exc_info:
        initialize_openapi_catalog(
            config,
            OpenAPIInitializationRequest(source=object(), name="Must not load"),
        )

    assert exc_info.value.code == "schema_already_initialized"


def test_parse_failure_rolls_back_catalog_writes(tmp_path: Path) -> None:
    from restscope.catalog import (
        CatalogInitializationError,
        OpenAPIInitializationRequest,
        initialize_openapi_catalog,
    )
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    with pytest.raises(CatalogInitializationError) as exc_info:
        initialize_openapi_catalog(
            config,
            OpenAPIInitializationRequest(
                source={
                    "openapi": "3.0.3",
                    "info": {"title": "Broken", "version": "1"},
                    "paths": [],
                },
                name="Broken",
            ),
        )

    assert exc_info.value.code == "openapi_parse_failed"
    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        assert uow.schemas.list() == []


def test_parse_warnings_are_retained_on_ready_schema(tmp_path: Path) -> None:
    from restscope.catalog import OpenAPIInitializationRequest, initialize_openapi_catalog
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    source = _petstore_spec()
    source["paths"]["/pets/{id}"]["get"]["operationId"] = "createPet"

    result = initialize_openapi_catalog(
        config,
        OpenAPIInitializationRequest(source=source, name="Petstore with warning"),
    )

    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        schema = uow.schemas.require(result.schema_id)

    assert result.warning_count == 1
    assert schema.parse_diagnostics_json["spec_warnings"][0]["code"] == "DUPLICATE_OPERATION_ID"


def test_catalog_separates_request_and_response_schema_refs(tmp_path: Path) -> None:
    from restscope.catalog import OpenAPIInitializationRequest, initialize_openapi_catalog
    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    source = _petstore_spec()
    source["components"] = {
        "schemas": {
            "PetCreate": {"type": "object", "properties": {"name": {"type": "string"}}},
            "Pet": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    }
    create = source["paths"]["/pets"]["post"]
    create["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/PetCreate"
    }
    create["responses"]["201"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Pet"
    }

    result = initialize_openapi_catalog(
        config,
        OpenAPIInitializationRequest(source=source, name="Petstore"),
    )

    factory = make_session_factory(create_engine_from_config(config.db))
    with UnitOfWork(factory) as uow:
        operation = uow.operations.get_by_schema_method_path(result.schema_id, "POST", "/pets")

    assert operation is not None
    assert operation.request_schema_refs == ["#/components/schemas/PetCreate"]
    assert operation.response_schema_refs == ["#/components/schemas/Pet"]


def test_catalog_tables_are_created_by_alembic(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from restscope.db.migrations import MIGRATIONS_DIR
    from restscope.db.session import create_engine_from_url

    db_path = tmp_path / "migrated.sqlite"
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(alembic_config, "head")

    inspector = inspect(create_engine_from_url(f"sqlite:///{db_path}"))
    assert "operation_edges" in inspector.get_table_names()
    assert {"normalized_spec_json", "catalog_status", "parse_diagnostics_json"}.issubset(
        {column["name"] for column in inspector.get_columns("schemas")}
    )

    command.downgrade(alembic_config, "0001_create_mvp_tables")
    downgraded = inspect(create_engine_from_url(f"sqlite:///{db_path}"))
    assert "operation_edges" not in downgraded.get_table_names()
    assert "catalog_status" not in {
        column["name"] for column in downgraded.get_columns("schemas")
    }


def test_ready_catalog_requires_the_unique_default_slot(tmp_path: Path) -> None:
    from sqlalchemy.exc import IntegrityError

    from restscope.db import UnitOfWork, create_engine_from_config
    from restscope.db.session import make_session_factory

    config = _config(tmp_path)
    factory = make_session_factory(create_engine_from_config(config.db))

    with pytest.raises(IntegrityError):
        with UnitOfWork(factory) as uow:
            uow.schemas.add(
                id="schema_invalid_ready",
                name="Invalid",
                spec_hash="invalid-ready",
                raw_spec_uri="memory://invalid",
                catalog_status="ready",
                catalog_slot=None,
            )
            uow.commit()
