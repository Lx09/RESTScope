from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect


def test_db_config_uses_short_environment_names(monkeypatch) -> None:
    monkeypatch.setenv("DB_URL", "sqlite:///custom.db")
    monkeypatch.setenv("DB_ECHO", "true")
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")

    from restscope.restscope_config import RESTScopeConfig

    config = RESTScopeConfig.from_environment()

    assert config.db.url == "sqlite:///custom.db"
    assert config.db.echo is True
    assert config.db.pool_size == 7
    assert config.db.max_overflow == 3


def test_base_metadata_creates_mvp_tables_in_sqlite() -> None:
    from restscope.db import Base, create_engine_from_url

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert {
        "schemas",
        "operations",
        "operation_intelligence",
        "agent_tasks",
        "campaigns",
        "test_observations",
        "artifacts",
        "context_snapshots",
        "event_log",
    }.issubset(set(inspector.get_table_names()))
    assert "idx_test_observations_dedupe" in {
        index["name"] for index in inspector.get_indexes("test_observations")
    }


def test_unit_of_work_commits_and_returns_record_dtos(tmp_path: Path) -> None:
    from restscope.db import Base, UnitOfWork, create_engine_from_url
    from restscope.db.records import SchemaRecord
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    with UnitOfWork(session_factory) as uow:
        schema = uow.schemas.add(
            id="schema_1",
            name="Petstore",
            version="1.0",
            spec_hash="hash-1",
            raw_spec_uri="file://petstore.json",
            operation_count=1,
        )
        uow.operations.add(
            id="op_1",
            schema_id=schema.id,
            operation_id="listPets",
            method="GET",
            path="/pets",
            tags=["pets"],
            card_json={"method": "GET", "path": "/pets"},
        )
        uow.intelligence.add(operation_id="op_1", schema_id=schema.id)
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        loaded = uow.schemas.require("schema_1")
        operations = uow.operations.list_by_schema("schema_1")

    assert isinstance(loaded, SchemaRecord)
    assert operations[0].path == "/pets"
    assert operations[0].tags == ["pets"]


def test_unit_of_work_rolls_back_on_error(tmp_path: Path) -> None:
    from restscope.db import Base, UnitOfWork, create_engine_from_url
    from restscope.db.exceptions import NotFoundError
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    with pytest.raises(RuntimeError):
        with UnitOfWork(session_factory) as uow:
            uow.schemas.add(
                id="schema_rollback",
                name="Rollback",
                spec_hash="hash-rollback",
                raw_spec_uri="file://rollback.json",
            )
            raise RuntimeError("boom")

    with UnitOfWork(session_factory) as uow:
        with pytest.raises(NotFoundError):
            uow.schemas.require("schema_rollback")


def test_explicit_rollback_prevents_persistence(tmp_path: Path) -> None:
    from restscope.db import Base, UnitOfWork, create_engine_from_url
    from restscope.db.exceptions import NotFoundError
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    with UnitOfWork(session_factory) as uow:
        uow.schemas.add(
            id="schema_rollback",
            name="Rollback",
            spec_hash="hash-rollback",
            raw_spec_uri="file://rollback.json",
        )
        uow.rollback()

    with UnitOfWork(session_factory) as uow:
        with pytest.raises(NotFoundError):
            uow.schemas.require("schema_rollback")


def test_observation_dedupe_and_task_optimistic_transition(tmp_path: Path) -> None:
    from restscope.db import Base, UnitOfWork, create_engine_from_url
    from restscope.db.exceptions import ConcurrencyError
    from restscope.db.session import make_session_factory

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    with UnitOfWork(session_factory) as uow:
        uow.schemas.add(
            id="schema_1",
            name="Petstore",
            spec_hash="hash-1",
            raw_spec_uri="file://petstore.json",
        )
        uow.tasks.add(
            id="task_1",
            schema_id="schema_1",
            state="new",
            goal_json={"goal": "smoke"},
            budget_json={"seconds": 60},
        )
        uow.campaigns.add(
            id="camp_1",
            task_id="task_1",
            schema_id="schema_1",
            status="running",
            campaign_type="smoke",
            campaign_spec_json={"type": "smoke"},
        )
        artifact = uow.artifacts.add(
            id="artifact_1",
            task_id="task_1",
            campaign_id="camp_1",
            artifact_type="schemathesis_json_result",
            artifact_uri="file://result.json",
            metadata_json={"format": "json"},
        )
        snapshot = uow.context_snapshots.add(
            id="ctx_1",
            task_id="task_1",
            schema_id="schema_1",
            role="planner",
            cycle_index=0,
            artifact_uri="file://context.json",
            prompt_version="v1",
            model_name="glm-4.5-air",
        )
        uow.observations.upsert_observed(
            id="obs_1",
            task_id="task_1",
            campaign_id="camp_1",
            schema_id="schema_1",
            observation_type="server_error",
            severity="high",
            dedupe_key="GET /pets 500",
        )
        updated = uow.observations.upsert_observed(
            id="obs_2",
            task_id="task_1",
            campaign_id="camp_1",
            schema_id="schema_1",
            observation_type="server_error",
            severity="high",
            dedupe_key="GET /pets 500",
        )
        task = uow.tasks.transition_state(
            task_id="task_1",
            expected_state="new",
            expected_version=0,
            new_state="planning",
        )
        uow.commit()

    assert updated.id == "obs_1"
    assert updated.occurrence_count == 2
    assert task.state == "planning"
    assert task.version == 1
    assert artifact.artifact_uri == "file://result.json"
    assert snapshot.role == "planner"

    with UnitOfWork(session_factory) as uow:
        with pytest.raises(ConcurrencyError):
            uow.tasks.transition_state(
                task_id="task_1",
                expected_state="new",
                expected_version=0,
                new_state="running",
            )


def test_event_log_append_and_alembic_upgrade(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from restscope.db import UnitOfWork
    from restscope.db.migrations import MIGRATIONS_DIR
    from restscope.db.session import create_engine_from_url, make_session_factory
    from restscope.db.records import EventLogRecord

    db_path = tmp_path / "migrated.sqlite"
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(alembic_config, "head")

    engine = create_engine_from_url(f"sqlite:///{db_path}")
    session_factory = make_session_factory(engine)
    with UnitOfWork(session_factory) as uow:
        event = uow.events.append(
            event_type="task_created",
            actor="controller",
            payload_json={"task_id": "task_1"},
        )
        with pytest.raises(TypeError):
            uow.events.delete(event.id)
        uow.commit()

    assert isinstance(event, EventLogRecord)
    assert event.id > 0
