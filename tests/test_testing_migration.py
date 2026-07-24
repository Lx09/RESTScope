from __future__ import annotations

import json
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
        "generator_config_revisions",
        "resources",
        "resource_aliases",
        "operation_resource_rules",
        "resource_identifiers",
        "resource_operation_usages",
        "resource_monitor_errors",
        "response_value_monitors",
        "response_observation_scalars",
        "response_observations",
        "response_value_sources",
        "response_values",
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
    assert {
        column["name"]
        for column in inspector.get_columns("generator_config_revisions")
    } == {
        "operation_key",
        "revision",
        "parent_revision",
        "lifecycle",
        "rollback_of_revision",
        "restored_from_revision",
        "hypothesis",
        "config",
        "evaluation",
        "evaluated_at",
        "created_at",
    }

    command.downgrade(config, "0001_create_schema_sources")
    assert set(inspect(engine).get_table_names()) == {"alembic_version", "schemas"}


def test_generator_revision_migration_backfills_existing_active_configs(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from restscope.db import create_engine_from_url
    from restscope.db.migrations import MIGRATIONS_DIR

    database = tmp_path / "backfill.sqlite"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "0003_create_resource_catalog")

    engine = create_engine_from_url(f"sqlite:///{database}")
    snapshot = {
        "operation_key": "GET /items/{itemId}",
        "method": "GET",
        "path": "/items/{itemId}",
        "available_media_types": [],
        "request_body_node_id": None,
        "nodes": [],
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO operation_generator_configs "
                "(operation_key, revision, snapshot, enabled, disabled_reasons, "
                "active_media_type, created_at, updated_at) "
                "VALUES (:operation_key, 7, :snapshot, 1, :disabled_reasons, "
                "NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "operation_key": "GET /items/{itemId}",
                "snapshot": json.dumps(snapshot),
                "disabled_reasons": "[]",
            },
        )
        connection.execute(
            text(
                "INSERT INTO input_generator_configs "
                "(input_node_id, operation_key, position, inclusion_probability, "
                "strategy, created_at, updated_at) "
                "VALUES (:input_node_id, :operation_key, 0, 1.0, :strategy, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "input_node_id": "input_item_id",
                "operation_key": "GET /items/{itemId}",
                "strategy": json.dumps(
                    {"type": "constant", "value": "existing"}
                ),
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT revision, parent_revision, lifecycle, config "
                "FROM generator_config_revisions "
                "WHERE operation_key = :operation_key"
            ),
            {"operation_key": "GET /items/{itemId}"},
        ).mappings().one()
    assert row["revision"] == 7
    assert row["parent_revision"] is None
    assert row["lifecycle"] == "accepted"
    payload = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
    assert payload["revision"] == 7
    assert payload["configs"] == [
        {
            "input_node_id": "input_item_id",
            "inclusion_probability": 1.0,
            "strategy": {"type": "constant", "value": "existing"},
        }
    ]
