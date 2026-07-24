from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def _document(title: str = "Pets") -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {
            "/pets": {
                "get": {
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def _raw(title: str = "Pets") -> str:
    return json.dumps(_document(title), indent=2)


def _catalog(tmp_path: Path):
    from restscope.catalog import SchemaCatalog
    from restscope.db import (
        Base,
        SqlAlchemySchemaUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'catalog.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    return SchemaCatalog(lambda: SqlAlchemySchemaUnitOfWork(factory)), engine


def test_schema_source_input_requires_exactly_one_nonblank_source() -> None:
    from restscope.catalog import SchemaSourceInput

    SchemaSourceInput(file_path=Path("openapi.yaml"))
    SchemaSourceInput(raw_content=_raw())

    with pytest.raises(ValidationError):
        SchemaSourceInput()
    with pytest.raises(ValidationError):
        SchemaSourceInput(file_path=Path("openapi.yaml"), raw_content=_raw())
    with pytest.raises(ValidationError):
        SchemaSourceInput(raw_content="  \n")


def test_register_preserves_verbatim_raw_content_and_lists_records(tmp_path: Path) -> None:
    from restscope.catalog import SchemaSourceInput

    catalog, _ = _catalog(tmp_path)
    raw = "\n" + _raw() + "\n"

    registered = catalog.register(SchemaSourceInput(raw_content=raw))

    assert registered.id.startswith("schema_")
    assert registered.file_path is None
    assert registered.raw_content == raw
    assert catalog.get(registered.id) == registered
    assert catalog.list() == [registered]
    assert catalog.load(registered.id).meta.title == "Pets"


def test_file_source_stores_only_absolute_path_and_loads_current_content(tmp_path: Path) -> None:
    from restscope.catalog import SchemaSourceInput

    catalog, _ = _catalog(tmp_path)
    source_path = tmp_path / "openapi.json"
    source_path.write_text(_raw("First"), encoding="utf-8")

    registered = catalog.register(SchemaSourceInput(file_path=source_path))

    assert registered.file_path == str(source_path.resolve())
    assert registered.raw_content is None
    source_path.write_text(_raw("Second"), encoding="utf-8")
    assert catalog.load(registered.id).meta.title == "Second"


def test_invalid_sources_do_not_create_or_replace_records(tmp_path: Path) -> None:
    from restscope.catalog import SchemaSourceInput, SchemaSourceValidationError

    catalog, _ = _catalog(tmp_path)

    with pytest.raises(SchemaSourceValidationError):
        catalog.register(SchemaSourceInput(raw_content="not: [valid"))
    parser_error_document = {
        "openapi": "3.0.3",
        "info": {"title": "Broken", "version": "1"},
        "paths": [],
    }
    with pytest.raises(SchemaSourceValidationError):
        catalog.register(SchemaSourceInput(raw_content=json.dumps(parser_error_document)))
    with pytest.raises(SchemaSourceValidationError):
        catalog.register(SchemaSourceInput(file_path=tmp_path / "missing.yaml"))
    assert catalog.list() == []

    original = catalog.register(SchemaSourceInput(raw_content=_raw("Original")))
    with pytest.raises(SchemaSourceValidationError):
        catalog.replace(original.id, SchemaSourceInput(raw_content="not: [valid"))
    assert catalog.get(original.id).raw_content == original.raw_content


def test_replace_changes_the_whole_source_and_missing_ids_are_explicit(tmp_path: Path) -> None:
    from restscope.catalog import SchemaNotFoundError, SchemaSourceInput

    catalog, _ = _catalog(tmp_path)
    original = catalog.register(SchemaSourceInput(raw_content=_raw("Original")))
    path = tmp_path / "replacement.yaml"
    path.write_text(
        "openapi: 3.0.3\ninfo:\n  title: Replacement\n  version: 1\npaths: {}\n",
        encoding="utf-8",
    )

    replaced = catalog.replace(original.id, SchemaSourceInput(file_path=path))

    assert replaced.id == original.id
    assert replaced.file_path == str(path.resolve())
    assert replaced.raw_content is None
    assert catalog.load(original.id).meta.title == "Replacement"
    with pytest.raises(SchemaNotFoundError):
        catalog.get("schema_missing")
    with pytest.raises(SchemaNotFoundError):
        catalog.replace("schema_missing", SchemaSourceInput(raw_content=_raw()))


def test_orm_metadata_contains_all_approved_persistence_tables(tmp_path: Path) -> None:
    from restscope.db import Base, create_engine_from_url

    assert set(Base.metadata.tables) == {
        "schemas",
        "generator_catalog_state",
        "operation_generator_configs",
        "input_generator_configs",
        "generator_config_revisions",
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
        "response_value_monitors",
        "response_value_sources",
        "response_values",
    }
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'constraint.sqlite'}")
    Base.metadata.create_all(engine)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schemas (id, file_path, raw_content, created_at, updated_at) "
                    "VALUES ('invalid', NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )


def test_alembic_chain_upgrades_and_downgrades_all_persistence_tables(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    from restscope.db import create_engine_from_url
    from restscope.db.migrations import MIGRATIONS_DIR

    db_path = tmp_path / "migrated.sqlite"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(config, "head")
    engine = create_engine_from_url(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "schemas",
        "generator_catalog_state",
        "operation_generator_configs",
        "input_generator_configs",
        "generator_config_revisions",
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
        "response_value_monitors",
        "response_value_sources",
        "response_values",
    }
    assert {column["name"] for column in inspector.get_columns("schemas")} == {
        "id",
        "file_path",
        "raw_content",
        "created_at",
        "updated_at",
    }

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}


def test_catalog_package_has_no_database_or_sqlalchemy_imports() -> None:
    import restscope.catalog as catalog_package
    import restscope.db as db_package

    catalog_root = Path(catalog_package.__file__).parent
    violations: list[str] = []
    for path in catalog_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in names):
                    violations.append(f"{path.name}:{node.lineno}:sqlalchemy")
                if any(name == "restscope.db" or name.startswith("restscope.db.") for name in names):
                    violations.append(f"{path.name}:{node.lineno}:restscope.db")

    assert violations == []
    assert not hasattr(db_package, "SchemaORM")


def test_public_builder_wires_configured_database(tmp_path: Path) -> None:
    from restscope import RESTScopeConfig, SchemaSourceInput, build_schema_catalog
    from restscope.db import Base, create_engine_from_config
    from restscope.restscope_config import DBConfig

    config = RESTScopeConfig.from_environment()
    config = replace(config, db=DBConfig(url=f"sqlite:///{tmp_path / 'builder.sqlite'}"))
    Base.metadata.create_all(create_engine_from_config(config.db))

    catalog = build_schema_catalog(config)
    record = catalog.register(SchemaSourceInput(raw_content=_raw("Builder")))

    assert catalog.load(record.id).meta.title == "Builder"
