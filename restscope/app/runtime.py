"""Expose RESTScope's small target and process lifecycle Interface.

``RESTScopeApp`` composes one production runtime, binds one validated target,
starts one blocking Orchestration loop, and releases all resources. Target parsing and
production wiring remain private so callers need to understand only the App's
four lifecycle operations and optional observer URL.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from restscope.config import RESTScopeConfig
from restscope.tools.context import ToolContextError

from .composition import _AppResources, _compose_app_resources
from .target import _build_target_context


class RESTScopeApp:
    """Own one production RESTScope runtime from construction through close."""

    def __init__(self, config: RESTScopeConfig) -> None:
        """Construct every production resource for one App lifetime.

        Args:
            config: Validated database, model, tracing, UI, and runtime
                configuration.

        Raises:
            BaseException: Production composition failed. Any resource already
                created is closed before the original failure is re-raised.
        """
        self._resources: _AppResources = _compose_app_resources(config)
        self._initialized = False
        self._started = False
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        env_file: str | Path | None = None,
    ) -> RESTScopeApp:
        """Load dotenv/process configuration and construct one production App.

        Args:
            env_file: Optional readable dotenv path. Process environment values
                retain the precedence defined by :class:`RESTScopeConfig`.

        Returns:
            A fully composed but not yet target-initialized App.
        """
        config = RESTScopeConfig.from_environment(
            Path(env_file).expanduser() if env_file else None
        )
        return cls(config)

    @property
    def ui_url(self) -> str | None:
        """Return the active loopback observer URL, or ``None`` when disabled."""
        return self._resources.ui_url

    def initialize(
        self,
        *,
        schema_source: Mapping[str, object],
        base_url: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Validate and bind the App's sole OpenAPI target.

        Args:
            schema_source: Closed file, URL, or inline OpenAPI source object.
            base_url: Required HTTP or HTTPS target base URL.
            headers: Optional copied App-lifetime target headers.

        Raises:
            ToolContextError: A target was already initialized.
            ValueError: Target headers or the OpenAPI document are invalid.
            TargetAPIError: The required target base URL is unsafe.
            RuntimeError: The App was already closed.
        """
        self._ensure_open()
        if self._initialized:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )
        context = _build_target_context(
            schema_source=schema_source,
            base_url=base_url,
            headers=headers,
        )
        self._resources.bind_target(context)
        self._initialized = True

    def start(self, focus: str | None = None) -> None:
        """Start the initialized Orchestration loop once and block until completion.

        Args:
            focus: Optional emphasis or restriction appended to RESTScope's
                fixed product mission for this run.
        """
        self._ensure_open()
        if not self._initialized:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        if self._started:
            raise RuntimeError("RESTScope Orchestration loop has already started")
        self._started = True
        marker = {"runtime": "orchestration", "mode": "blocking", "focus": focus}
        observer = self._resources.run_observer
        if observer is not None:
            observer.begin_run(marker)
        try:
            with self._resources.tracing.span(
                "RESTScopeApp.start",
                kind="CHAIN",
                input_value=marker,
            ) as span:
                self._resources.run_orchestration(focus)
                terminal = {"runtime": "orchestration", "status": "completed"}
                span.set_output(terminal)
                span.set_attribute("restscope.agent.status", "completed")
        except KeyboardInterrupt:
            if observer is not None:
                observer.interrupt_run()
            raise
        except BaseException as exc:
            if observer is not None:
                observer.end_run(error=exc)
            raise
        if observer is not None:
            observer.end_run(terminal)

    def close(self) -> None:
        """Close every App-owned resource once and clear target state."""
        if self._closed:
            return
        try:
            self._resources.close()
        finally:
            self._closed = True

    def _ensure_open(self) -> None:
        """Reject lifecycle operations after the App has closed."""
        if self._closed:
            raise RuntimeError("RESTScopeApp is already closed")
