"""Protect the OpenAPI Audit and the reduced persistent database topology."""

from __future__ import annotations

from pathlib import Path


BUSINESS_TABLES = {
    "openapi_current",
    "openapi_change_events",
    "operations",
    "resources",
    "operation_resource_edges",
    "resource_instances",
    "observations",
    "operation_input_sources",
    "abstract_test_cases",
}


def _alembic_config(database: Path):
    """Point Alembic at one isolated SQLite file."""
    from alembic.config import Config

    from restscope.db.migrations import MIGRATIONS_DIR

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def test_orm_metadata_contains_only_the_approved_response_monitor_tables() -> None:
    """The fresh database exposes the approved facts and no retired value pools."""
    from restscope.db import Base

    assert set(Base.metadata.tables) == BUSINESS_TABLES
    assert all(
        not name.startswith(("smoke_", "generator_", "input_generator"))
        for name in Base.metadata.tables
    )
    assert {
        "response_value_pools",
        "response_value_pool_sources",
        "response_value_pool_values",
        "response_observations",
        "response_observation_scalars",
    }.isdisjoint(Base.metadata.tables)


def test_response_monitor_tables_enforce_status_source_and_prior_shapes() -> None:
    """Database constraints reject impossible observations and input sources."""
    from sqlalchemy import CheckConstraint

    from restscope.db import Base

    check_names = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    expected_suffixes = {
        "observation_success_status",
        "operation_input_source_success_status",
        "operation_input_source_consume_type",
        "operation_input_source_alpha",
        "operation_input_source_beta",
        "operation_resource_edge_alpha",
        "operation_resource_edge_beta",
    }
    assert all(
        any(name.endswith(suffix) for name in check_names)
        for suffix in expected_suffixes
    )


def test_response_monitor_natural_primary_keys_match_the_approved_model() -> None:
    """Composite identities prevent duplicate roles, instances, and sources."""

    from restscope.db import Base

    expected = {
        "operations": ("operation_id",),
        "resources": ("resource_id",),
        "operation_resource_edges": (
            "operation_id",
            "resource_id",
            "role",
        ),
        "resource_instances": ("resource_type", "resource_instance_id"),
        "observations": ("observation_id",),
        "operation_input_sources": (
            "consumer_operation_id",
            "consumer_input_node_id",
            "producer_operation_id",
            "status_code",
            "media_type",
            "selector",
            "field_name",
            "consume_type",
        ),
        "abstract_test_cases": ("abstract_test_case_id",),
    }

    for table_name, columns in expected.items():
        table = Base.metadata.tables[table_name]
        assert tuple(item.name for item in table.primary_key.columns) == columns

    edge = Base.metadata.tables["operation_resource_edges"]
    source = Base.metadata.tables["operation_input_sources"]
    assert {"_alpha", "_beta"} <= {item.name for item in edge.columns}
    assert {"_alpha", "_beta"} <= {item.name for item in source.columns}


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
