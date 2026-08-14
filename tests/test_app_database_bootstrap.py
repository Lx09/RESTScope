"""Protect fresh App database ownership after Operation Smoke retirement."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

BUSINESS_TABLES = {
    "openapi_current",
    "openapi_change_events",
    "operations",
    "resources",
    "operation_resource_edges",
    "resource_instances",
    "batches",
    "observations",
    "operation_input_sources",
    "abstract_test_cases",
    "oracle_assessments",
}


def _config(database_url: str):
    """Build one default App configuration with an isolated database URL."""
    from restscope.config import DBConfig, RESTScopeConfig

    return replace(RESTScopeConfig.from_environment(), db=DBConfig(url=database_url))


def test_default_app_creates_only_the_approved_persistent_business_tables(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Generation state is App-memory only; SQLite retains approved audit facts."""
    from sqlalchemy import inspect

    from restscope import RESTScopeApp
    from restscope.db import create_engine_from_url

    monkeypatch.chdir(tmp_path)
    config = _config("sqlite:///runtime.sqlite")
    app = RESTScopeApp(config)
    try:
        assert set(inspect(create_engine_from_url(config.db.url)).get_table_names()) == {
            "alembic_version",
            *BUSINESS_TABLES,
        }
        result = app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(
                    {
                        "openapi": "3.0.3",
                        "info": {"title": "Bootstrap", "version": "1"},
                        "paths": {
                            "/health": {
                                "get": {
                                    "responses": {"200": {"description": "ok"}}
                                }
                            }
                        },
                    }
                ),
            },
            base_url="https://api.test",
        )
        assert result is None
    finally:
        app.close()


@pytest.mark.parametrize(
    "database_url",
    (
        "sqlite:///:memory:",
        "sqlite:///file:runtime.sqlite?uri=true",
        "postgresql://localhost/restscope",
        "not a database url",
    ),
)
def test_default_app_rejects_non_fresh_local_file_databases(
    database_url: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """The default composition path remains restricted to a fresh SQLite file."""
    from restscope import RESTScopeApp
    from restscope.db import UnsupportedDatabaseURLError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(UnsupportedDatabaseURLError):
        RESTScopeApp(_config(database_url))


def test_existing_database_is_preserved_and_rejected(tmp_path: Path) -> None:
    """Fresh bootstrap never overwrites a caller-owned file."""
    from restscope import RESTScopeApp
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "occupied.sqlite"
    database.write_bytes(b"caller data")
    with pytest.raises(DatabaseAlreadyExistsError):
        RESTScopeApp(_config(f"sqlite:///{database}"))
    assert database.read_bytes() == b"caller data"


def test_successful_close_preserves_fresh_database(tmp_path: Path) -> None:
    """Closing releases resources but does not delete approved audit evidence."""
    from restscope import RESTScopeApp
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "runtime.sqlite"
    app = RESTScopeApp(_config(f"sqlite:///{database}"))
    app.close()
    assert database.is_file()
    with pytest.raises(DatabaseAlreadyExistsError):
        RESTScopeApp(_config(f"sqlite:///{database}"))


def test_failed_default_composition_removes_database_and_closes_tracing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A late default-wiring failure leaves no half-owned App resources."""
    from restscope import RESTScopeApp
    from restscope.app import composition
    from restscope.observability import TracingRuntime

    database = tmp_path / "incomplete.sqlite"
    tracing = TracingRuntime.disabled()
    tracing_closed: list[bool] = []
    monkeypatch.setattr(tracing, "close", lambda: tracing_closed.append(True))
    monkeypatch.setattr(
        composition,
        "_build_app_tracing_runtime",
        lambda _config: tracing,
    )
    monkeypatch.setattr(
        composition,
        "build_harness",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("wiring failed")),
    )

    with pytest.raises(RuntimeError, match="wiring failed"):
        RESTScopeApp(_config(f"sqlite:///{database}"))

    assert tracing_closed == [True]
    assert not database.exists()


def test_close_releases_later_resources_when_agent_cleanup_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """An Agent close error cannot strand Context or tracing resources."""
    from sqlalchemy import create_engine

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.app.composition import _AppResources
    from restscope.harness import build_harness
    from restscope.observability import TracingRuntime
    from restscope.request_generation import RequestGenerationConfigStore

    runtime = build_harness()
    tracing = TracingRuntime.disabled()
    tracing_closed: list[bool] = []
    monkeypatch.setattr(
        runtime,
        "close_agents",
        lambda: (_ for _ in ()).throw(RuntimeError("agent close failed")),
    )
    monkeypatch.setattr(tracing, "close", lambda: tracing_closed.append(True))
    resources = _AppResources(
        harness=runtime,
        tracing=tracing,
        catalog=APIBehaviorCatalog(lambda: None),
        generation_store=RequestGenerationConfigStore(),
        database_engine=create_engine("sqlite:///:memory:"),
    )

    with pytest.raises(RuntimeError, match="agent close failed"):
        resources.close()

    assert tracing_closed == [True]
