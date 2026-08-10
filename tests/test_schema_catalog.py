"""Protect the OpenAPI Audit and the reduced persistent database topology."""

from __future__ import annotations

from pathlib import Path


BUSINESS_TABLES = {
    "openapi_current",
    "openapi_change_events",
    "resources",
    "resource_aliases",
    "operation_resource_rules",
    "resource_identifiers",
    "resource_identifier_definitions",
    "resource_operation_usages",
    "resource_monitor_errors",
    "response_value_pools",
    "response_value_pool_sources",
    "response_value_pool_values",
    "response_observations",
    "response_observation_scalars",
}


def _alembic_config(database: Path):
    """Point Alembic at one isolated SQLite file."""
    from alembic.config import Config

    from restscope.db.migrations import MIGRATIONS_DIR

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def test_orm_metadata_contains_exactly_fourteen_business_tables() -> None:
    """No retired Smoke or Generator persistence remains in ORM metadata."""
    from restscope.db import Base

    assert set(Base.metadata.tables) == BUSINESS_TABLES
    assert all(
        not name.startswith(("smoke_", "generator_", "input_generator"))
        for name in Base.metadata.tables
    )
    assert {
        "response_value_monitors",
        "response_value_sources",
        "response_values",
    }.isdisjoint(Base.metadata.tables)


def test_evidence_tables_enforce_scalar_status_position_and_rule_shapes() -> None:
    """Database constraints reject impossible evidence even if an Adapter regresses."""
    from sqlalchemy import CheckConstraint

    from restscope.db import Base

    check_names = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expected_suffixes = {
        "resource_rule_shape",
        "response_pool_scalar_type",
        "response_observation_http_status",
        "response_observation_scalar_type",
        "response_observation_scalar_position",
    }
    assert all(
        any(name.endswith(suffix) for name in check_names)
        for suffix in expected_suffixes
    )


def test_alembic_baseline_creates_and_drops_the_exact_current_schema(
    tmp_path: Path,
) -> None:
    """A fresh migration matches ORM metadata and remains reversible."""
    from alembic import command
    from sqlalchemy import inspect

    from restscope.db import create_engine_from_url

    database = tmp_path / "baseline.sqlite"
    config = _alembic_config(database)
    command.upgrade(config, "head")
    engine = create_engine_from_url(f"sqlite:///{database}")
    assert set(inspect(engine).get_table_names()) == {"alembic_version", *BUSINESS_TABLES}

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
