"""App lifecycle and optional-hosting failure contracts for the live UI."""

from __future__ import annotations

import socket
import sys

from dataclasses import replace
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from restscope.harness import AgentRuntimeDefinition


def _interrupting_agent_definition() -> "AgentRuntimeDefinition":
    """Build one production-shaped Main definition that simulates Ctrl-C."""
    from restscope.agent import AgentProfile
    from restscope.harness import AgentRuntimeDefinition
    from restscope.llm import LLMClient, LLMModelConfig
    from restscope.llm.registry import LLMProviderRegistry

    class InterruptingProvider:
        """Raise KeyboardInterrupt from the normal Main Agent model seam."""

        name = "interrupting"

        def invoke(self, request):
            """Interrupt the blocking Main loop before it can return output."""

            del request
            raise KeyboardInterrupt()

    registry = LLMProviderRegistry()
    registry.register(InterruptingProvider())
    return AgentRuntimeDefinition(
        profiles=(AgentProfile(name="main", model_config_name="thinking"),),
        models=(
            LLMModelConfig(
                name="thinking",
                provider="interrupting",
                model="interrupting-model",
                max_tokens=128,
                context_window_tokens=2_048,
            ),
        ),
        client=LLMClient(registry),
    )


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
    from restscope.config import DBConfig, RESTScopeConfig, UIConfig

    from restscope.ui import UIService

    service = UIService(observer=object(), port=9988, static_root=tmp_path)
    service.closed = False

    def close_service() -> None:
        service.closed = True

    service.close = close_service
    monkeypatch.setattr(
        "restscope.app.composition.start_ui_service",
        lambda **_kwargs: service,
    )
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'ui.sqlite'}"),
        ui=UIConfig(enabled=True, port=9988),
    )
    tracing = TracingRuntime.disabled()
    monkeypatch.setattr(
        "restscope.app.composition._build_app_tracing_runtime",
        lambda _config: tracing,
    )
    app = RESTScopeApp(config)

    assert app.ui_url == "http://127.0.0.1:9988"
    assert tracing.run_observer is not None
    app.close()
    assert service.closed is True
    assert tracing.run_observer.snapshot()["run"] is None


def test_keyboard_interrupt_stops_the_main_loop_and_keeps_ui_available(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Ctrl-C preserves the stopped snapshot until the App is closed."""
    from restscope.app import RESTScopeApp
    from restscope.observability import TracingRuntime
    from restscope.config import DBConfig, RESTScopeConfig, UIConfig

    from restscope.ui import UIService

    service = UIService(observer=object(), port=9987, static_root=tmp_path)
    service.closed = False
    service.close = lambda: setattr(service, "closed", True)
    monkeypatch.setattr(
        "restscope.app.composition.start_ui_service",
        lambda **_kwargs: service,
    )
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'interrupt.sqlite'}"),
        ui=UIConfig(enabled=True, port=9987),
    )
    tracing = TracingRuntime.disabled()
    interrupting = _interrupting_agent_definition()
    monkeypatch.setattr(
        "restscope.app.composition._build_app_tracing_runtime",
        lambda _config: tracing,
    )
    monkeypatch.setattr(
        "restscope.app.composition._build_agent_runtime_definition",
        lambda *_args, **_kwargs: interrupting,
    )
    app = RESTScopeApp(config)
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
    from restscope.config import DBConfig, RESTScopeConfig, UIConfig

    monkeypatch.setattr(
        "restscope.app.composition.start_ui_service",
        lambda **_kwargs: None,
    )
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / "missing.env"),
        db=DBConfig(url=f"sqlite:///{tmp_path / 'failed-ui.sqlite'}"),
        ui=UIConfig(enabled=True, port=9989),
    )
    tracing = TracingRuntime.disabled()

    monkeypatch.setattr(
        "restscope.app.composition._build_app_tracing_runtime",
        lambda _config: tracing,
    )
    app = RESTScopeApp(config)

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
