"""Protect the one-shot OpenAPI audit catalog and final database topology."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


BUSINESS_TABLES = {
    "openapi_current",
    "openapi_change_events",
    "input_generator_configs",
    "operation_constraints",
    "generator_change_events",
    "resources",
    "resource_aliases",
    "operation_resource_rules",
    "resource_identifiers",
    "resource_operation_usages",
    "resource_monitor_errors",
    "response_value_monitors",
    "response_value_sources",
    "response_values",
    "response_observations",
    "response_observation_scalars",
    "smoke_failures",
    "smoke_solve_attempts",
    "smoke_solve_attempt_parameters",
}

EXPECTED_COLUMNS = {
    "openapi_current": {"singleton_id", "document", "created_at", "updated_at"},
    "openapi_change_events": {
        "id", "operation_key", "status_code", "media_type", "changes",
        "response_before", "response_after", "created_at",
    },
    "input_generator_configs": {
        "input_node_id", "operation_key", "position", "inclusion_probability",
        "strategy", "created_at", "updated_at",
    },
    "operation_constraints": {
        "id", "operation_key", "owner_input_node_ids", "kind", "expression",
        "created_at",
    },
    "generator_change_events": {
        "id", "solve_attempt_id", "operation_key", "reason",
        "generator_changes", "constraint_changes", "created_at",
    },
    "resources": {"id", "canonical_name", "normalized_name", "created_at"},
    "resource_aliases": {
        "normalized_alias", "resource_id", "alias", "created_at",
    },
    "operation_resource_rules": {
        "id", "operation_key", "group_path", "resource_id", "has_resource",
        "id_field_name", "id_selector", "access_mode", "classification_source",
        "created_at", "updated_at",
    },
    "resource_identifiers": {
        "id", "resource_id", "value_type", "value_text", "first_seen_at",
        "last_seen_at",
    },
    "resource_operation_usages": {
        "identifier_id", "operation_rule_id", "latest_seen_at",
    },
    "resource_monitor_errors": {
        "operation_key", "group_path", "resource_id", "code", "message",
        "issues", "created_at", "updated_at",
    },
    "response_value_monitors": {
        "value_name", "consumer_operation_key", "consumer_input_node_id",
        "parameter_name", "expected_type", "created_at", "updated_at",
    },
    "response_value_sources": {
        "value_name", "producer_operation_key", "status_code", "media_type",
        "selector", "field_name", "created_at",
    },
    "response_values": {
        "value_name", "value_type", "value_text", "first_seen_at", "last_seen_at",
    },
    "response_observations": {
        "id", "operation_key", "status_code", "media_type", "observed_at",
    },
    "response_observation_scalars": {
        "observation_id", "selector", "value_type", "value_text", "position",
    },
    "smoke_failures": {
        "id", "failure_key", "operation_key", "normalized_messages", "summary",
        "suspected_input_node_ids", "occurrence_count", "first_seen_at",
        "last_seen_at", "last_status_code",
    },
    "smoke_solve_attempts": {
        "id", "failure_id", "round_number", "outcome", "reason",
        "root_cause", "created_at",
    },
    "smoke_solve_attempt_parameters": {
        "solve_attempt_id", "input_node_id", "cause_summary", "position",
    },
}

EXPECTED_PRIMARY_KEYS = {
    "openapi_current": ["singleton_id"],
    "openapi_change_events": ["id"],
    "input_generator_configs": ["input_node_id"],
    "operation_constraints": ["id"],
    "generator_change_events": ["id"],
    "resources": ["id"],
    "resource_aliases": ["normalized_alias"],
    "operation_resource_rules": ["id"],
    "resource_identifiers": ["id"],
    "resource_operation_usages": ["identifier_id", "operation_rule_id"],
    "resource_monitor_errors": ["operation_key", "group_path"],
    "response_value_monitors": ["value_name"],
    "response_value_sources": [
        "value_name", "producer_operation_key", "status_code", "media_type", "selector",
    ],
    "response_values": ["value_name", "value_type", "value_text"],
    "response_observations": ["id"],
    "response_observation_scalars": [
        "observation_id", "selector", "value_type", "value_text",
    ],
    "smoke_failures": ["id"],
    "smoke_solve_attempts": ["id"],
    "smoke_solve_attempt_parameters": ["solve_attempt_id", "input_node_id"],
}


def _document(title: str = "Pets") -> dict:
    """Build one small normalized-document-shaped OpenAPI mapping."""

    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0.0"},
        "paths": {
            "/pets": {
                "get": {
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def _catalog(tmp_path: Path):
    """Create an isolated current OpenAPI catalog without running App startup."""

    from restscope.catalog import OpenAPICatalog
    from restscope.db import (
        Base,
        SqlAlchemyOpenAPIUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'catalog.sqlite'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    return OpenAPICatalog(lambda: SqlAlchemyOpenAPIUnitOfWork(factory)), engine


def test_openapi_catalog_initializes_once_and_returns_isolated_copies(
    tmp_path: Path,
) -> None:
    """The audit catalog owns one current document and never exposes mutable JSON."""

    catalog, _ = _catalog(tmp_path)
    supplied = _document()

    catalog.initialize(supplied)
    supplied["info"]["title"] = "Changed outside"
    exported = catalog.current_document()
    exported["info"]["title"] = "Changed copy"

    assert catalog.current_document()["info"]["title"] == "Pets"
    with pytest.raises(ValueError, match="already initialized"):
        catalog.initialize(_document("Second"))


def test_openapi_change_updates_current_and_appends_filterable_event(
    tmp_path: Path,
) -> None:
    """One response change commits the replacement document and audit row together."""

    from restscope.catalog import OpenAPIChangeEventWrite

    catalog, _ = _catalog(tmp_path)
    catalog.initialize(_document())
    changed = _document()
    changed["paths"]["/pets"]["get"]["responses"]["201"] = {
        "description": "created"
    }

    record = catalog.record_change(
        document=changed,
        event=OpenAPIChangeEventWrite(
            operation_key="GET /pets",
            status_code=201,
            media_type="application/json",
            changes=["response:201"],
            response_before=None,
            response_after={"description": "created"},
        ),
    )

    assert record.id.startswith("openapi_change_")
    assert "201" in catalog.current_document()["paths"]["/pets"]["get"]["responses"]
    assert catalog.list_changes("GET /pets") == [record]
    assert catalog.list_changes("POST /pets") == []


def test_orm_metadata_contains_exactly_the_approved_19_business_tables(
    tmp_path: Path,
) -> None:
    """Removed snapshots, source rows, and old Smoke tables cannot return silently."""

    from restscope.db import Base, create_engine_from_url

    assert set(Base.metadata.tables) == BUSINESS_TABLES
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'constraints.sqlite'}")
    Base.metadata.create_all(engine)

    # The singleton CHECK is a representative executable boundary assertion;
    # the following inspector test covers all declared keys and relationships.
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO openapi_current "
                    "(singleton_id, document, created_at, updated_at) VALUES "
                    "(2, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )


def test_final_schema_declares_natural_keys_checks_indexes_and_foreign_keys(
    tmp_path: Path,
) -> None:
    """Inspect every field/key plus all required checks, indexes, and ownership FKs."""

    from restscope.db import Base, create_engine_from_url

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'shape.sqlite'}")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    for table_name in sorted(BUSINESS_TABLES):
        assert {
            item["name"] for item in inspector.get_columns(table_name)
        } == EXPECTED_COLUMNS[table_name]
        assert inspector.get_pk_constraint(table_name)[
            "constrained_columns"
        ] == EXPECTED_PRIMARY_KEYS[table_name]

    expected_unique_columns = {
        "generator_change_events": {frozenset({"solve_attempt_id"})},
        "resources": {frozenset({"normalized_name"})},
        "operation_resource_rules": {
            frozenset({"operation_key", "group_path"})
        },
        "resource_identifiers": {
            frozenset({"resource_id", "value_type", "value_text"})
        },
        "response_value_monitors": {
            frozenset({"consumer_operation_key", "consumer_input_node_id"})
        },
        "smoke_failures": {frozenset({"failure_key"})},
    }
    for table_name, expected in expected_unique_columns.items():
        actual = {
            frozenset(item["column_names"])
            for item in inspector.get_unique_constraints(table_name)
        }
        assert actual == expected

    input_checks = inspector.get_check_constraints("input_generator_configs")
    assert any("inclusion_probability" in item["sqltext"] for item in input_checks)
    assert any(
        "singleton_id = 1" in item["sqltext"]
        for item in inspector.get_check_constraints("openapi_current")
    )
    assert any(
        "occurrence_count >= 1" in item["sqltext"]
        for item in inspector.get_check_constraints("smoke_failures")
    )
    assert inspector.get_check_constraints("smoke_solve_attempts") == []
    attempt_columns = {
        item["name"]: item
        for item in inspector.get_columns("smoke_solve_attempts")
    }
    assert attempt_columns["reason"]["nullable"] is False
    assert attempt_columns["root_cause"]["nullable"] is True

    expected_foreign_keys = {
        "generator_change_events": {("solve_attempt_id", "smoke_solve_attempts")},
        "resource_aliases": {("resource_id", "resources")},
        "operation_resource_rules": {("resource_id", "resources")},
        "resource_identifiers": {("resource_id", "resources")},
        "resource_operation_usages": {
            ("identifier_id", "resource_identifiers"),
            ("operation_rule_id", "operation_resource_rules"),
        },
        "resource_monitor_errors": {("resource_id", "resources")},
        "response_value_sources": {("value_name", "response_value_monitors")},
        "response_values": {("value_name", "response_value_monitors")},
        "response_observation_scalars": {
            ("observation_id", "response_observations")
        },
        "smoke_solve_attempts": {("failure_id", "smoke_failures")},
        "smoke_solve_attempt_parameters": {
            ("solve_attempt_id", "smoke_solve_attempts"),
            ("input_node_id", "input_generator_configs"),
        },
    }
    for table_name in sorted(BUSINESS_TABLES):
        actual = {
            (item["constrained_columns"][0], item["referred_table"])
            for item in inspector.get_foreign_keys(table_name)
        }
        assert actual == expected_foreign_keys.get(table_name, set())

    required_indexes = {
        "openapi_change_events": {"ix_openapi_change_events_operation_created"},
        "input_generator_configs": {"ix_input_generator_configs_operation"},
        "operation_constraints": {"ix_operation_constraints_operation"},
        "generator_change_events": {"ix_generator_change_events_operation_created"},
        "response_value_sources": {"ix_response_value_sources_producer"},
        "response_values": {"ix_response_values_pool_last_seen"},
        "response_observations": {"ix_response_observations_operation_time"},
        "smoke_failures": {"ix_smoke_failures_operation"},
        "smoke_solve_attempts": {"ix_smoke_solve_attempts_failure_created"},
    }
    for table_name, required in required_indexes.items():
        assert required <= {
            item["name"] for item in inspector.get_indexes(table_name)
        }


def test_every_project_sqlite_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    """The Engine connection hook rejects orphan rows instead of trusting declarations."""

    from restscope.db import Base, create_engine_from_url

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'foreign-keys.sqlite'}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO resource_aliases "
                    "(normalized_alias, resource_id, alias, created_at) VALUES "
                    "('missing', 'resource_missing', 'missing', CURRENT_TIMESTAMP)"
                )
            )


def test_alembic_baseline_upgrades_and_downgrades_exact_final_schema(
    tmp_path: Path,
) -> None:
    """The single baseline creates only the final tables and can return to base."""

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
    assert set(inspect(engine).get_table_names()) == {
        "alembic_version",
        *BUSINESS_TABLES,
    }

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}


def test_catalog_package_has_no_database_or_sqlalchemy_imports() -> None:
    """The catalog contracts remain independent from their SQLAlchemy adapter."""

    import restscope.catalog as catalog_package

    catalog_root = Path(catalog_package.__file__).parent
    violations: list[str] = []
    for path in catalog_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in names):
                violations.append(f"{path.name}:{node.lineno}:sqlalchemy")
            if any(name == "restscope.db" or name.startswith("restscope.db.") for name in names):
                violations.append(f"{path.name}:{node.lineno}:restscope.db")

    assert violations == []


def test_public_builder_wires_the_configured_openapi_catalog(tmp_path: Path) -> None:
    """The composition helper returns a working database-backed audit catalog."""

    from restscope import RESTScopeConfig, build_openapi_catalog
    from restscope.db import Base, create_engine_from_config
    from restscope.restscope_config import DBConfig

    config = RESTScopeConfig.from_environment()
    config = replace(config, db=DBConfig(url=f"sqlite:///{tmp_path / 'builder.sqlite'}"))
    Base.metadata.create_all(create_engine_from_config(config.db))

    catalog = build_openapi_catalog(config)
    catalog.initialize(_document("Builder"))

    assert catalog.current_document()["info"]["title"] == "Builder"
