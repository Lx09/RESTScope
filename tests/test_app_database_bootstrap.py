"""Protect fresh App database ownership after Operation Smoke retirement."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest


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


def _config(database_url: str):
    """Build one default App configuration with an isolated database URL."""
    from restscope.config import DBConfig, RESTScopeConfig

    return replace(RESTScopeConfig.from_environment(), db=DBConfig(url=database_url))


def test_default_app_creates_only_the_nine_persistent_business_tables(
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
        context = app.initialize(
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
            }
        )
        assert context.ir.operations
        assert app.export_current_openapi()["info"]["title"] == "Bootstrap"
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
    from restscope.harness import build_harness

    runtime = build_harness()
    database = tmp_path / "unused.sqlite"
    app = RESTScopeApp.from_config(
        _config(f"sqlite:///{database}"),
        harness_runtime=runtime,
    )
    try:
        assert not database.exists()
        with pytest.raises(RuntimeError, match="no API Behavior Catalog"):
            app.export_current_openapi()
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
        RESTScopeApp.from_config(_config(f"sqlite:///{database}"))

    assert tracing_closed == [True]
    assert not database.exists()


def test_failed_injected_harness_binding_does_not_close_caller_tracing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Construction adopts caller tracing only after the whole App succeeds."""
    from restscope import RESTScopeApp
    from restscope.harness import build_harness
    from restscope.observability import TracingRuntime

    runtime = build_harness()
    tracing = TracingRuntime.disabled()
    tracing_closed: list[bool] = []
    monkeypatch.setattr(tracing, "close", lambda: tracing_closed.append(True))
    monkeypatch.setattr(
        runtime,
        "bind_tracing_runtime",
        lambda _tracing: (_ for _ in ()).throw(RuntimeError("binding failed")),
    )

    with pytest.raises(RuntimeError, match="binding failed"):
        RESTScopeApp.from_config(
            _config(f"sqlite:///{tmp_path / 'unused.sqlite'}"),
            harness_runtime=runtime,
            tracing_runtime=tracing,
        )

    assert tracing_closed == []


def test_close_releases_later_resources_when_agent_cleanup_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A Main-Agent close error cannot strand Context or tracing resources."""
    from restscope import RESTScopeApp
    from restscope.harness import build_harness
    from restscope.observability import TracingRuntime
    from restscope.tools.context import ToolContextError

    runtime = build_harness()
    tracing = TracingRuntime.disabled()
    tracing_closed: list[bool] = []
    monkeypatch.setattr(
        runtime,
        "close_main_agent",
        lambda: (_ for _ in ()).throw(RuntimeError("agent close failed")),
    )
    monkeypatch.setattr(tracing, "close", lambda: tracing_closed.append(True))
    app = RESTScopeApp.from_config(
        _config(f"sqlite:///{tmp_path / 'unused.sqlite'}"),
        harness_runtime=runtime,
        tracing_runtime=tracing,
    )
    app.initialize(
        schema_source={
            "kind": "inline",
            "format": "json",
            "content": json.dumps(
                {
                    "openapi": "3.0.3",
                    "info": {"title": "Cleanup", "version": "1"},
                    "paths": {
                        "/health": {
                            "get": {"responses": {"200": {"description": "ok"}}}
                        }
                    },
                }
            ),
        }
    )

    with pytest.raises(RuntimeError, match="agent close failed"):
        app.close()

    with pytest.raises(ToolContextError) as exc_info:
        runtime.require_context()
    assert exc_info.value.code == "tool_context_not_initialized"
    assert tracing_closed == [True]
    with pytest.raises(RuntimeError, match="closed"):
        app.start()
