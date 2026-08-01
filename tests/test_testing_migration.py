"""Regression contracts for RESTScope's single fresh-database baseline."""

from __future__ import annotations

from pathlib import Path


def _alembic_config(database: Path):
    """Point Alembic at one isolated SQLite file."""
    from alembic.config import Config

    from restscope.db.migrations import MIGRATIONS_DIR

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def test_current_baseline_creates_current_generator_and_solve_attempt_tables(
    tmp_path: Path,
) -> None:
    """Scenario: a new App database receives every current persistence area."""
    from alembic import command
    from sqlalchemy import inspect

    from restscope.db import create_engine_from_url

    database = tmp_path / "baseline.sqlite"
    command.upgrade(_alembic_config(database), "head")
    inspector = inspect(create_engine_from_url(f"sqlite:///{database}"))

    assert {
        "smoke_failures",
        "smoke_solve_attempts",
        "smoke_solve_attempt_parameters",
        "operation_constraints",
        "generator_change_events",
    } <= set(inspector.get_table_names())
    assert {
        column["name"]
        for column in inspector.get_columns("input_generator_configs")
    } == {
        "input_node_id",
        "operation_key",
        "position",
        "inclusion_probability",
        "strategy",
        "created_at",
        "updated_at",
    }


def test_database_stamped_with_retired_revision_is_explicitly_incompatible(
    tmp_path: Path,
) -> None:
    """Scenario: the deleted exploratory chain is not silently upgraded."""
    import pytest
    from alembic import command
    from alembic.util import CommandError
    from sqlalchemy import text

    from restscope.db import create_engine_from_url

    database = tmp_path / "retired.sqlite"
    engine = create_engine_from_url(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('0006_create_response_observation_history')"
            )
        )

    with pytest.raises(CommandError, match="Can't locate revision"):
        command.upgrade(_alembic_config(database), "head")
