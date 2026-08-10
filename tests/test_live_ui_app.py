"""App lifecycle and optional-hosting failure contracts for the live UI."""

from __future__ import annotations

import socket
import sys

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


class _InjectedCapabilities:
    """Provide only the composition hooks used by an injected App runtime."""

    mcp_host = None

    def __init__(self, *, main_agent=None) -> None:
        self.tracing_runtime = None
        self.context_cleared = False
        self.context = None
        self.main_agent = main_agent

    def bind_tracing_runtime(self, runtime) -> None:
        """Record the App's trace facade as a normal injected runtime would."""
        self.tracing_runtime = runtime

    def clear_context(self) -> None:
        """Record App cleanup without owning a real ToolContext."""
        self.context_cleared = True

    def bind_context(self, context) -> None:
        """Retain the initialized target context for App run scenarios."""
        self.context = context

    def require_context(self):
        """Return the initialized context or match the production error contract."""
        from restscope.tools.context import ToolContextError

        if self.context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        return self.context

    def start_main_agent(self, profile_name):
        """Return the injected Main loop after checking its stable name."""
        assert profile_name == "main"
        if self.main_agent is None:
            raise RuntimeError("No Main Agent was injected")
        return self.main_agent

    def close_main_agent(self) -> None:
        """Close the injected loop when it exposes the normal lifecycle hook."""
        close = getattr(self.main_agent, "close", None)
        if callable(close):
            close()


_ONE_GET_SCHEMA = json.dumps(
    {
        "openapi": "3.0.3",
        "info": {"title": "Lifecycle test", "version": "1.0.0"},
        "paths": {
            "/health": {
                "get": {
                    "responses": {"200": {"description": "Healthy"}},
                }
            }
        },
    }
)


def test_app_exposes_ui_url_and_closes_the_started_service(monkeypatch, tmp_path: Path) -> None:
    """Scenario: enabled hosting exposes only the actual started loopback URL."""
    from restscope.app import RESTScopeApp
    from restscope.observability import TracingRuntime
    from restscope.config import RESTScopeConfig, UIConfig

    service = SimpleNamespace(url="http://127.0.0.1:9988", closed=False)

    def close_service() -> None:
        service.closed = True

    service.close = close_service
    monkeypatch.setattr("restscope.ui.start_ui_service", lambda **_kwargs: service)
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        ui=UIConfig(enabled=True, port=9988),
    )
    tracing = TracingRuntime.disabled()
    capabilities = _InjectedCapabilities()
    app = RESTScopeApp(
        config=config,
        harness_runtime=capabilities,
        tracing_runtime=tracing,
    )

    assert app.ui_url == "http://127.0.0.1:9988"
    assert tracing.run_observer is not None
    app.close()
    assert service.closed is True
    assert capabilities.context_cleared is True
    assert tracing.run_observer.snapshot()["run"] is None


def test_keyboard_interrupt_stops_the_main_loop_and_keeps_ui_available(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Ctrl-C preserves the stopped snapshot until the App is closed."""
    from restscope.app import RESTScopeApp
    from restscope.observability import TracingRuntime
    from restscope.config import RESTScopeConfig, UIConfig

    class InterruptingMain:
        """Represent a blocking Main loop interrupted by its local caller."""

        def start(self):
            raise KeyboardInterrupt()

    service = SimpleNamespace(url="http://127.0.0.1:9987", closed=False)
    service.close = lambda: setattr(service, "closed", True)
    monkeypatch.setattr("restscope.ui.start_ui_service", lambda **_kwargs: service)
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        ui=UIConfig(enabled=True, port=9987),
    )
    tracing = TracingRuntime.disabled()
    app = RESTScopeApp(
        config=config,
        harness_runtime=_InjectedCapabilities(main_agent=InterruptingMain()),
        tracing_runtime=tracing,
    )
    app.initialize(
        schema_source={
            "kind": "inline",
            "format": "json",
            "content": _ONE_GET_SCHEMA,
        },
        base_url="https://api.test",
    )

    with pytest.raises(KeyboardInterrupt):
        app.start()

    stopped = tracing.run_observer.snapshot()
    assert stopped["run"]["status"] == "stopped"
    assert stopped["run"]["ended_at"] is not None
    # The interrupting test double emits no model or Tool spans, so the stopped
    # Main lifetime correctly has an empty semantic timeline.
    assert stopped["events"] == []
    assert app.ui_url == "http://127.0.0.1:9987"
    assert service.closed is False

    app.close()
    assert service.closed is True


def test_app_continues_without_collection_when_ui_startup_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: an optional server failure disables UI but not App construction."""
    from restscope.app import RESTScopeApp
    from restscope.observability import TracingRuntime
    from restscope.config import RESTScopeConfig, UIConfig

    monkeypatch.setattr("restscope.ui.start_ui_service", lambda **_kwargs: None)
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        ui=UIConfig(enabled=True, port=9989),
    )
    tracing = TracingRuntime.disabled()

    app = RESTScopeApp(
        config=config,
        harness_runtime=_InjectedCapabilities(),
        tracing_runtime=tracing,
    )

    assert app.ui_url is None
    assert tracing.run_observer is None
    app.close()


def test_ui_service_contains_missing_optional_dependency(monkeypatch, tmp_path: Path) -> None:
    """Scenario: missing Uvicorn is a warning result instead of an App exception."""
    from restscope.ui.server import UIService

    monkeypatch.setitem(sys.modules, "uvicorn", None)
    service = UIService(observer=object(), port=9990, static_root=tmp_path)

    assert service.start(timeout_seconds=0.1) is False


def test_ui_service_contains_a_loopback_port_conflict() -> None:
    """Scenario: an occupied configured port cannot terminate RESTScope startup."""
    from restscope.observability import LiveRunObserver
    from restscope.ui.server import STATIC_ROOT, UIService

    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = int(occupied.getsockname()[1])
        service = UIService(
            observer=LiveRunObserver(),
            port=port,
            static_root=STATIC_ROOT,
        )

        assert service.start(timeout_seconds=0.5) is False
