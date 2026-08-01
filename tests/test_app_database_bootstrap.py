"""Regression scenarios for app database bootstrap. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


class _TrackingTracingRuntime:
    def __init__(self, *, falsey: bool = False) -> None:
        self.closed = False
        self.falsey = falsey
        self.redactor = SimpleNamespace(register_secrets=lambda _values: None)

    def __bool__(self) -> bool:
        return not self.falsey

    def close(self) -> None:
        self.closed = True


def _config(database_url: str):
    from restscope.restscope_config import DBConfig, RESTScopeConfig

    return replace(
        RESTScopeConfig.from_environment(),
        db=DBConfig(url=database_url),
    )


def _build_default_app(config):
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    return RESTScopeApp.from_config(
        config,
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
    )


def test_default_app_creates_migrated_fresh_sqlite_and_normalizes_relative_url(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that default app creates migrated fresh sqlite and normalizes relative url."""
    from sqlalchemy import inspect

    from restscope.db import create_engine_from_url

    monkeypatch.chdir(tmp_path)
    app = _build_default_app(_config("sqlite:///runtime.sqlite"))
    database = tmp_path / "runtime.sqlite"

    try:
        assert app.config.db.url == f"sqlite:///{database}"
        assert set(inspect(create_engine_from_url(app.config.db.url)).get_table_names()) == {
            "alembic_version",
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
    finally:
        app.close()

    assert database.is_file()


def test_default_app_preserves_absolute_sqlite_location(tmp_path: Path) -> None:
    """Scenario: verify that default app preserves absolute sqlite location."""
    database = tmp_path / "absolute.sqlite"
    app = _build_default_app(_config(f"sqlite:///{database}"))

    try:
        assert app.config.db.url == f"sqlite:///{database}"
    finally:
        app.close()


def test_public_database_bootstrap_error_preserves_code_and_message() -> None:
    """Scenario: verify that public database bootstrap error preserves code and message."""
    from restscope.db import DatabaseBootstrapError

    error = DatabaseBootstrapError("database_test", "Database test failure")

    assert error.code == "database_test"
    assert str(error) == "Database test failure"


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:///:memory:",
        "sqlite:///file:runtime.sqlite?uri=true",
        "sqlite:///runtime.sqlite?uri=false&uri=true",
        "sqlite:///runtime.sqlite?mode=memory&uri=false&uri=false",
        "postgresql://localhost/restscope",
        "not a database url",
    ],
)
def test_default_app_rejects_unsupported_database_urls(
    database_url: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that default app rejects unsupported database urls."""
    from restscope.db import UnsupportedDatabaseURLError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(UnsupportedDatabaseURLError) as exc_info:
        _build_default_app(_config(database_url))

    assert exc_info.value.code == "database_url_unsupported"
    assert str(exc_info.value) == (
        "Default RESTScope runtime requires a local file SQLite database"
    )


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite://host/path.db",
        "sqlite://user@host/path.db",
        "sqlite://user:password@host:123/path.db",
    ],
)
def test_default_app_rejects_sqlite_authority_before_creating_a_file(
    database_url: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that default app rejects sqlite authority before creating a file."""
    import os

    from restscope.db import UnsupportedDatabaseURLError

    monkeypatch.chdir(tmp_path)
    open_calls: list[object] = []
    original_open = os.open

    def tracking_open(path, *args, **kwargs):
        open_calls.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("restscope.db.bootstrap.os.open", tracking_open)

    with pytest.raises(UnsupportedDatabaseURLError) as exc_info:
        _build_default_app(_config(database_url))

    assert exc_info.value.code == "database_url_unsupported"
    assert str(exc_info.value) == (
        "Default RESTScope runtime requires a local file SQLite database"
    )
    assert open_calls == []
    assert not (tmp_path / "path.db").exists()


@pytest.mark.parametrize(
    "existing_kind",
    ["file", "empty", "directory", "symlink", "broken_symlink"],
)
def test_default_app_rejects_every_existing_database_path_without_changing_it(
    existing_kind: str,
    tmp_path: Path,
) -> None:
    """Scenario: verify that default app rejects every existing database path without changing it."""
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "occupied.sqlite"
    expected_file_content: bytes | None = None
    symlink_target: Path | None = None
    if existing_kind == "file":
        expected_file_content = b"existing database-like content"
        database.write_bytes(expected_file_content)
    elif existing_kind == "empty":
        expected_file_content = b""
        database.touch()
    elif existing_kind == "directory":
        database.mkdir()
        (database / "marker").write_text("preserve", encoding="utf-8")
    elif existing_kind == "symlink":
        symlink_target = tmp_path / "target.sqlite"
        symlink_target.write_bytes(b"target content")
        database.symlink_to(symlink_target)
    else:
        symlink_target = tmp_path / "missing-target.sqlite"
        database.symlink_to(symlink_target)

    with pytest.raises(DatabaseAlreadyExistsError) as exc_info:
        _build_default_app(_config(f"sqlite:///{database}"))

    assert exc_info.value.code == "database_already_exists"
    assert str(exc_info.value) == "Configured SQLite database already exists"
    if expected_file_content is not None:
        assert database.read_bytes() == expected_file_content
    elif existing_kind == "directory":
        assert (database / "marker").read_text(encoding="utf-8") == "preserve"
    else:
        assert database.is_symlink()
        assert symlink_target is not None
        if existing_kind == "symlink":
            assert symlink_target.read_bytes() == b"target content"
        else:
            assert not symlink_target.exists()


def test_successful_app_close_preserves_database_and_second_start_rejects_it(
    tmp_path: Path,
) -> None:
    """Scenario: verify that successful app close preserves database and second start rejects it."""
    from restscope.db import DatabaseAlreadyExistsError

    database = tmp_path / "one-shot.sqlite"
    config = _config(f"sqlite:///{database}")
    app = _build_default_app(config)
    app.close()

    assert database.is_file()
    with pytest.raises(DatabaseAlreadyExistsError):
        _build_default_app(config)


def test_complete_injected_capability_runtime_skips_database_validation() -> None:
    """Scenario: verify that complete injected capability runtime skips database validation."""
    runtime = SimpleNamespace()
    config = _config("postgresql://ignored.example/restscope")

    app = _build_app_with_runtime(config, runtime)
    try:
        # App resolves an absent RANDOM_SEED once even when the capability
        # runtime is injected; every later collaborator observes that value.
        assert app.config.random.seed is not None
        assert app.config.db is config.db
        assert app.capability_runtime is runtime
    finally:
        app.close()


def test_falsey_injected_capability_runtime_is_not_replaced() -> None:
    """Scenario: verify that falsey injected capability runtime is not replaced."""
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    class FalseyRuntime:
        def __init__(self) -> None:
            self.mcp_host = None

        def clear_context(self) -> None:
            """Mirror the current CapabilityRuntime cleanup seam."""

        def __bool__(self) -> bool:
            return False

    runtime = FalseyRuntime()
    app = RESTScopeApp.from_config(
        _config("postgresql://ignored.example/restscope"),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        capability_runtime=runtime,
    )
    try:
        assert app.capability_runtime is runtime
    finally:
        app.close()


def test_falsey_injected_tracing_runtime_is_preserved(tmp_path: Path) -> None:
    """Scenario: verify that falsey injected tracing runtime is preserved."""
    trace_runtime = _TrackingTracingRuntime(falsey=True)
    database = tmp_path / "falsey-tracing.sqlite"
    app = _build_default_app_with_tracing(
        _config(f"sqlite:///{database}"),
        trace_runtime,
    )
    try:
        assert app.tracing_runtime is trace_runtime
    finally:
        app.close()

    assert trace_runtime.closed is True


def test_falsey_injected_tracing_runtime_is_not_closed_on_factory_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that falsey injected tracing runtime is not closed on factory failure."""
    trace_runtime = _TrackingTracingRuntime(falsey=True)
    database = tmp_path / "falsey-tracing-failure.sqlite"
    monkeypatch.setattr(
        "restscope.app._build_app_tracing_runtime",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("injected tracing runtime must not be replaced")
        ),
    )
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("runtime failed")),
    )

    with pytest.raises(RuntimeError, match="runtime failed"):
        _build_default_app_with_tracing(
            _config(f"sqlite:///{database}"),
            trace_runtime,
        )

    assert trace_runtime.closed is False
    assert not database.exists()


def test_only_injected_smoke_coordinator_still_enforces_fresh_sqlite() -> None:
    """Scenario: verify that only injected smoke agent still enforces fresh sqlite."""
    from restscope import RESTScopeApp
    from restscope.db import UnsupportedDatabaseURLError
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    with pytest.raises(UnsupportedDatabaseURLError):
        RESTScopeApp.from_config(
            _config("sqlite:///:memory:"),
            operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        )


def test_direct_default_app_construction_bootstraps_database(tmp_path: Path) -> None:
    """Scenario: verify that direct default app construction bootstraps database."""
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    database = tmp_path / "direct.sqlite"
    app = RESTScopeApp(
        config=_config(f"sqlite:///{database}"),
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
    )
    try:
        assert app.config.db.url == f"sqlite:///{database}"
        assert database.is_file()
    finally:
        app.close()


def test_from_environment_bootstraps_relative_database_from_startup_cwd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that from environment bootstraps relative database from startup cwd."""
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "app.env"
    env_file.write_text("DB_URL=sqlite:///nested/app.sqlite\n", encoding="utf-8")
    app = RESTScopeApp.from_environment(
        env_file=env_file,
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
    )
    try:
        assert app.config.db.url == f"sqlite:///{tmp_path / 'nested/app.sqlite'}"
    finally:
        app.close()


def test_migration_failure_removes_only_files_created_by_this_bootstrap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that migration failure removes only files created by this bootstrap."""
    from restscope.db import DatabaseBootstrapError

    database = tmp_path / "failed-migration.sqlite"
    preexisting_sidecar = Path(f"{database}-wal")
    missing_sidecar_target = tmp_path / "missing-sidecar-target"
    preexisting_sidecar.symlink_to(missing_sidecar_target)

    def fail_migration(*_args, **_kwargs):
        Path(f"{database}-journal").write_bytes(b"created")
        Path(f"{database}-shm").write_bytes(b"created")
        raise RuntimeError("migration detail must remain internal")

    monkeypatch.setattr("restscope.db.bootstrap.command.upgrade", fail_migration)

    with pytest.raises(DatabaseBootstrapError) as exc_info:
        _build_default_app(_config(f"sqlite:///{database}"))

    assert exc_info.value.code == "database_bootstrap_failed"
    assert str(exc_info.value) == "Failed to prepare configured SQLite database"
    assert not database.exists()
    assert not Path(f"{database}-journal").exists()
    assert not Path(f"{database}-shm").exists()
    assert preexisting_sidecar.is_symlink()
    assert not missing_sidecar_target.exists()


def test_migration_failure_preserves_replacement_database_and_its_sidecar(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that migration failure preserves replacement database and its sidecar."""
    from restscope.db import DatabaseBootstrapError

    database = tmp_path / "replaced.sqlite"
    replacement_sidecar = Path(f"{database}-wal")

    def replace_database_then_fail(*_args, **_kwargs):
        database.unlink()
        database.write_bytes(b"replacement database")
        replacement_sidecar.write_bytes(b"replacement sidecar")
        raise RuntimeError("migration failed after replacement")

    monkeypatch.setattr(
        "restscope.db.bootstrap.command.upgrade",
        replace_database_then_fail,
    )

    with pytest.raises(DatabaseBootstrapError):
        _build_default_app(_config(f"sqlite:///{database}"))

    assert database.read_bytes() == b"replacement database"
    assert replacement_sidecar.read_bytes() == b"replacement sidecar"


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt(), SystemExit(7)],
    ids=["keyboard_interrupt", "system_exit"],
)
def test_migration_process_interrupt_cleans_claimed_database_and_sidecars(
    interrupt: BaseException,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that migration process interrupt cleans claimed database and sidecars."""
    database = tmp_path / "interrupted-migration.sqlite"
    sidecar = Path(f"{database}-wal")

    def interrupt_migration(*_args, **_kwargs):
        sidecar.write_bytes(b"created by interrupted migration")
        raise interrupt

    monkeypatch.setattr(
        "restscope.db.bootstrap.command.upgrade",
        interrupt_migration,
    )

    with pytest.raises(type(interrupt)):
        _build_default_app(_config(f"sqlite:///{database}"))

    assert not database.exists()
    assert not sidecar.exists()


def test_default_runtime_construction_failure_removes_created_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that default runtime construction failure removes created database."""
    database = tmp_path / "failed-runtime.sqlite"
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("runtime failed")),
    )

    with pytest.raises(RuntimeError, match="runtime failed"):
        _build_default_app(_config(f"sqlite:///{database}"))

    assert not database.exists()
    assert not Path(f"{database}-journal").exists()
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_direct_app_keyboard_interrupt_cleans_owned_database_and_tracing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that direct app keyboard interrupt cleans owned database and tracing."""
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    trace_runtime = _TrackingTracingRuntime()
    database = tmp_path / "direct-interrupted.sqlite"
    monkeypatch.setattr(
        "restscope.app._build_app_tracing_runtime",
        lambda _config: trace_runtime,
    )
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        RESTScopeApp(
            config=_config(f"sqlite:///{database}"),
            operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        )

    assert trace_runtime.closed is True
    assert not database.exists()


def test_from_config_keyboard_interrupt_cleans_owned_resources(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that from config keyboard interrupt cleans owned resources."""
    from restscope import RESTScopeApp

    trace_runtime = _TrackingTracingRuntime()
    database = tmp_path / "factory-interrupted.sqlite"
    host = SimpleNamespace(closed=False)
    host.close = lambda: setattr(host, "closed", True)
    runtime = SimpleNamespace(
        mcp_host=host,
        target_http_tool=object(),
        require_operation=lambda _key: None,
        require_context=lambda: None,
    )
    monkeypatch.setattr(
        "restscope.app._build_app_tracing_runtime",
        lambda _config: trace_runtime,
    )
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(
        RESTScopeApp,
        "__init__",
        lambda self, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        RESTScopeApp.from_config(
            _config(f"sqlite:///{database}"),
        )

    assert host.closed is True
    assert trace_runtime.closed is True
    assert not database.exists()


def test_file_claim_failure_after_creation_removes_created_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that file claim failure after creation removes created database."""
    import os

    from restscope.db import DatabaseBootstrapError

    database = tmp_path / "failed-claim.sqlite"
    original_close = os.close

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError("close failed")

    monkeypatch.setattr("restscope.db.bootstrap.os.close", close_then_fail)

    with pytest.raises(DatabaseBootstrapError) as exc_info:
        _build_default_app(_config(f"sqlite:///{database}"))

    assert exc_info.value.code == "database_bootstrap_failed"
    assert not database.exists()


def test_fstat_failure_closes_descriptor_and_removes_claimed_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that fstat failure closes descriptor and removes claimed database."""
    import os

    from restscope.db import DatabaseBootstrapError

    database = tmp_path / "failed-fstat.sqlite"
    descriptor: list[int] = []
    original_fstat = os.fstat

    def fail_fstat(fd: int):
        descriptor.append(fd)
        raise OSError("fstat failed")

    monkeypatch.setattr("restscope.db.bootstrap.os.fstat", fail_fstat)

    with pytest.raises(DatabaseBootstrapError) as exc_info:
        _build_default_app(_config(f"sqlite:///{database}"))

    assert exc_info.value.code == "database_bootstrap_failed"
    assert len(descriptor) == 1
    with pytest.raises(OSError):
        original_fstat(descriptor[0])
    assert not database.exists()


def test_fstat_keyboard_interrupt_closes_descriptor_and_removes_claimed_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that fstat keyboard interrupt closes descriptor and removes claimed database."""
    import os

    database = tmp_path / "interrupted-fstat.sqlite"
    descriptor: list[int] = []
    original_fstat = os.fstat

    def interrupt_fstat(fd: int):
        descriptor.append(fd)
        raise KeyboardInterrupt()

    monkeypatch.setattr("restscope.db.bootstrap.os.fstat", interrupt_fstat)

    with pytest.raises(KeyboardInterrupt):
        _build_default_app(_config(f"sqlite:///{database}"))

    assert len(descriptor) == 1
    with pytest.raises(OSError):
        original_fstat(descriptor[0])
    assert not database.exists()


@pytest.mark.parametrize(
    "interrupt",
    [KeyboardInterrupt(), SystemExit(9)],
    ids=["keyboard_interrupt", "system_exit"],
)
def test_close_interrupt_removes_claimed_database_without_reclosing_descriptor(
    interrupt: BaseException,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that close interrupt removes claimed database without reclosing descriptor."""
    import os

    database = tmp_path / "interrupted-close.sqlite"
    close_calls: list[int] = []
    original_close = os.close
    original_fstat = os.fstat

    def close_then_interrupt(fd: int) -> None:
        close_calls.append(fd)
        original_close(fd)
        raise interrupt

    monkeypatch.setattr("restscope.db.bootstrap.os.close", close_then_interrupt)

    with pytest.raises(type(interrupt)):
        _build_default_app(_config(f"sqlite:///{database}"))

    assert len(close_calls) == 1
    with pytest.raises(OSError):
        original_fstat(close_calls[0])
    assert not database.exists()


def test_parent_path_file_is_reported_as_database_bootstrap_failed(
    tmp_path: Path,
) -> None:
    """Scenario: verify that parent path file is reported as database bootstrap failed."""
    from restscope.db import DatabaseBootstrapError

    parent = tmp_path / "parent"
    parent.write_text("not a directory", encoding="utf-8")
    database = parent / "runtime.sqlite"

    with pytest.raises(DatabaseBootstrapError) as exc_info:
        _build_default_app(_config(f"sqlite:///{database}"))

    assert type(exc_info.value) is DatabaseBootstrapError
    assert exc_info.value.code == "database_bootstrap_failed"
    assert str(exc_info.value) == "Failed to prepare configured SQLite database"
    assert parent.read_text(encoding="utf-8") == "not a directory"


def test_nul_database_path_is_reported_as_database_bootstrap_failed() -> None:
    """Scenario: verify that nul database path is reported as database bootstrap failed."""
    from restscope.db import DatabaseBootstrapError

    with pytest.raises(DatabaseBootstrapError) as exc_info:
        _build_default_app(_config("sqlite:///invalid\x00.sqlite"))

    assert type(exc_info.value) is DatabaseBootstrapError
    assert exc_info.value.code == "database_bootstrap_failed"
    assert str(exc_info.value) == "Failed to prepare configured SQLite database"


def test_smoke_coordinator_construction_failure_closes_runtime_and_removes_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that smoke agent construction failure closes runtime and removes database."""
    from restscope import RESTScopeApp
    database = tmp_path / "failed-analyzer.sqlite"
    host = SimpleNamespace(closed=False)
    host.close = lambda: setattr(host, "closed", True)
    runtime = SimpleNamespace(
        mcp_host=host,
        target_http_tool=object(),
        require_operation=lambda _key: None,
        require_context=lambda: None,
    )
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(
        "restscope.app.build_api_behavior_monitor_coordinator",
        lambda *_args, **_kwargs: SimpleNamespace(catalog=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "restscope.app.build_operation_smoke_coordinator",
        lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(RuntimeError("smoke failed")),
    )

    with pytest.raises(RuntimeError, match="smoke failed"):
        RESTScopeApp.from_config(
            _config(f"sqlite:///{database}"),
        )

    assert host.closed is True
    assert not database.exists()


def test_from_config_defaults_to_local_operation_smoke_without_mcp_host(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that from config defaults to local operation smoke without mcp host."""
    from restscope import RESTScopeApp
    from restscope.observability import TracingRuntime

    database = tmp_path / "smoke-default.sqlite"
    smoke_coordinator = SimpleNamespace(run=lambda *_args, **_kwargs: None)
    runtime = SimpleNamespace(
        clear_context=lambda: None,
        operation_testing_service=None,
    )
    capability_calls = []

    monkeypatch.setattr(
        "restscope.app.build_api_behavior_monitor_coordinator",
        lambda *_args, **_kwargs: SimpleNamespace(catalog=SimpleNamespace()),
    )
    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **kwargs: capability_calls.append(kwargs) or runtime,
    )
    monkeypatch.setattr(
        "restscope.app.build_operation_smoke_coordinator",
        lambda *_args, **_kwargs: smoke_coordinator,
    )

    app = RESTScopeApp.from_config(
        _config(f"sqlite:///{database}"),
        tracing_runtime=TracingRuntime.disabled(),
    )
    try:
        assert app.operation_smoke_coordinator is smoke_coordinator
        assert not hasattr(app, "operation_runner")
        assert not hasattr(app, "dependency_analyzer")
        assert len(capability_calls) == 1
    finally:
        app.close()


def test_app_constructor_failure_removes_created_database(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: verify that app constructor failure removes created database."""
    from restscope import RESTScopeApp

    database = tmp_path / "failed-app.sqlite"
    host = SimpleNamespace(closed=False)
    host.close = lambda: setattr(host, "closed", True)
    runtime = SimpleNamespace(
        mcp_host=host,
        target_http_tool=object(),
        require_operation=lambda _key: None,
        require_context=lambda: None,
    )

    def fail_constructor(self, **_kwargs):
        del self
        raise RuntimeError("app failed")

    monkeypatch.setattr(
        "restscope.app.build_capabilities",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(RESTScopeApp, "__init__", fail_constructor)

    with pytest.raises(RuntimeError, match="app failed"):
        RESTScopeApp.from_config(
            _config(f"sqlite:///{database}"),
        )

    assert host.closed is True
    assert not database.exists()


def _build_app_with_runtime(config, runtime):
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    return RESTScopeApp.from_config(
        config,
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        capability_runtime=runtime,
    )


def _build_default_app_with_tracing(config, tracing_runtime):
    from restscope import RESTScopeApp
    from tests._operation_smoke_coordinator_stub import PassingOperationSmokeCoordinator

    return RESTScopeApp.from_config(
        config,
        operation_smoke_coordinator=PassingOperationSmokeCoordinator(),
        tracing_runtime=tracing_runtime,
    )
