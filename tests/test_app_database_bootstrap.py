"""Protect fresh App database ownership after Operation Smoke retirement."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def _config(database_url: str):
    """Build one default App configuration with an isolated database URL."""
    from restscope.config import DBConfig, RESTScopeConfig

    return replace(RESTScopeConfig.from_environment(), db=DBConfig(url=database_url))


def test_default_app_creates_only_the_fourteen_persistent_business_tables(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Generation state is App-memory only; SQLite retains Monitor/Audit facts."""
    from sqlalchemy import inspect

    from restscope import RESTScopeApp
    from restscope.db import create_engine_from_url

    monkeypatch.chdir(tmp_path)
    app = RESTScopeApp.from_config(_config("sqlite:///runtime.sqlite"))
    try:
        assert set(inspect(create_engine_from_url(app.config.db.url)).get_table_names()) == {
            "alembic_version",
            *BUSINESS_TABLES,
        }
        assert app.request_generation_store is not None
        assert app.request_generation_patch_runtime is not None
        assert app.operation_testing_service is not None
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
        RESTScopeApp.from_config(_config(database_url))


def test_existing_database_is_preserved_and_rejected(tmp_path: Path) -> None:
    """Fresh bootstrap never overwrites a caller-owned file."""
    from restscope import RESTScopeApp
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "occupied.sqlite"
    database.write_bytes(b"caller data")
    with pytest.raises(DatabaseAlreadyExistsError):
        RESTScopeApp.from_config(_config(f"sqlite:///{database}"))
    assert database.read_bytes() == b"caller data"


def test_injected_harness_skips_database_creation(tmp_path: Path) -> None:
    """An embedder-owned runtime does not silently acquire App persistence."""
    from restscope import RESTScopeApp

    runtime = SimpleNamespace(
        bind_tracing_runtime=lambda _runtime: None,
        clear_context=lambda: None,
        mcp_host=None,
    )
    database = tmp_path / "unused.sqlite"
    app = RESTScopeApp.from_config(
        _config(f"sqlite:///{database}"),
        harness_runtime=runtime,
    )
    try:
        assert not database.exists()
        assert app.request_generation_store is None
        assert app.operation_testing_service is None
    finally:
        app.close()


def test_successful_close_preserves_fresh_database(tmp_path: Path) -> None:
    """Closing releases resources but does not delete approved audit evidence."""
    from restscope import RESTScopeApp
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "runtime.sqlite"
    app = RESTScopeApp.from_config(_config(f"sqlite:///{database}"))
    app.close()
    assert database.is_file()
    with pytest.raises(DatabaseAlreadyExistsError):
        RESTScopeApp.from_config(_config(f"sqlite:///{database}"))
