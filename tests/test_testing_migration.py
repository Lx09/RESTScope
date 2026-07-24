from __future__ import annotations

from pathlib import Path


def test_generator_config_migration_upgrades_existing_schema_catalog(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from restscope.db import create_engine_from_url
    from restscope.db.migrations import MIGRATIONS_DIR

    database = tmp_path / "migration.sqlite"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")

    command.upgrade(config, "0001_create_schema_sources")
    command.upgrade(config, "head")

    engine = create_engine_from_url(f"sqlite:///{database}")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "schemas",
        "generator_catalog_state",
        "operation_generator_configs",
        "input_generator_configs",
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
    }
    assert {column["name"] for column in inspector.get_columns("input_generator_configs")} == {
        "input_node_id",
        "operation_key",
        "position",
        "inclusion_probability",
        "strategy",
        "created_at",
        "updated_at",
    }
    assert {column["name"] for column in inspector.get_columns("operation_generator_configs")} == {
        "operation_key",
        "revision",
        "snapshot",
        "enabled",
        "disabled_reasons",
        "active_media_type",
        "created_at",
        "updated_at",
    }

    command.downgrade(config, "0001_create_schema_sources")
    assert set(inspect(engine).get_table_names()) == {"alembic_version", "schemas"}
