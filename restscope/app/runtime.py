"""Expose RESTScope's target initialization and App-lifetime control flow.

``RESTScopeApp`` accepts configuration, initializes one OpenAPI target, starts
one blocking Main Agent, exposes bounded audit reads, and closes every adopted
resource. Private composition supplies the implementation graph, so callers do
not need to navigate database, Monitor, HTTP, Tool, UI, or Agent wiring.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from restscope.api_behavior_monitor.catalog import OpenAPIChangeEventRecord
from restscope.config import RESTScopeConfig
from restscope.harness import HarnessRuntime
from restscope.observability import TracingRuntime
from restscope.openapi_parser import OpenAPIParser
from restscope.tools.context import ToolContext, ToolContextError

from .composition import _AppResources, _compose_app_resources


class _SchemaSourceModel(BaseModel):
    """Reject unknown fields in one App initialization source."""

    model_config = ConfigDict(extra="forbid")


class _FileSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one local filesystem path."""

    kind: Literal["file"]
    path: str


class _UrlSchemaSource(_SchemaSourceModel):
    """Read an OpenAPI document from one explicit URL."""

    kind: Literal["url"]
    url: str


class _InlineSchemaSource(_SchemaSourceModel):
    """Parse an OpenAPI document supplied directly by the App caller."""

    kind: Literal["inline"]
    format: Literal["yaml", "json"] = "yaml"
    content: str


_SchemaSource = Annotated[
    _FileSchemaSource | _UrlSchemaSource | _InlineSchemaSource,
    Field(discriminator="kind"),
]


class RESTScopeApp:
    """Own one initialized target and its complete runtime lifecycle.

    Construction opens or adopts long-lived resources. ``initialize`` binds one
    immutable-by-convention target, ``start`` runs the Main Agent at most once,
    and ``close`` releases the complete App. Callers may inspect only stable
    configuration, Context, tracing, UI URL, and audit results.
    """

    def __init__(
        self,
        *,
        config: RESTScopeConfig,
        harness_runtime: HarnessRuntime | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Compose defaults or adopt a caller-built concrete Harness.

        Args:
            config: Validated configuration for this App lifetime.
            harness_runtime: Optional real Harness built by the caller. When
                supplied, default database and domain composition are skipped.
            tracing_runtime: Optional tracing runtime adopted by the App after
                successful construction.

        Raises:
            BaseException: Startup failures are re-raised after App-created
                resources and incomplete database files are cleaned up.
        """
        self._resources: _AppResources = _compose_app_resources(
            config,
            harness_runtime=harness_runtime,
            tracing_runtime=tracing_runtime,
        )
        self._main_loop_started = False
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: str | Path | None = None,
        harness_runtime: HarnessRuntime | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Load environment configuration and build one App.

        Args:
            env_file: Optional dotenv file to read before process environment.
            harness_runtime: Optional caller-built concrete Harness.
            tracing_runtime: Optional caller-built tracing runtime.

        Returns:
            A fully constructed, not-yet-initialized App.
        """
        config = RESTScopeConfig.from_environment(
            Path(env_file).expanduser() if env_file else None
        )
        return cls.from_config(
            config,
            harness_runtime=harness_runtime,
            tracing_runtime=tracing_runtime,
        )

    @classmethod
    def from_config(
        cls,
        config: RESTScopeConfig,
        *,
        harness_runtime: HarnessRuntime | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Build one App through the same composition path as direct use."""
        return cls(
            config=config,
            harness_runtime=harness_runtime,
            tracing_runtime=tracing_runtime,
        )

    @property
    def config(self) -> RESTScopeConfig:
        """Return the normalized immutable App configuration."""
        return self._resources.config

    @property
    def tool_context(self) -> ToolContext | None:
        """Return initialized target state, or ``None`` before initialization."""
        return self._resources.tool_context

    @property
    def tracing_runtime(self) -> TracingRuntime:
        """Return the App-owned tracing runtime until the App is closed."""
        return self._resources.tracing

    @property
    def ui_url(self) -> str | None:
        """Return the active loopback observer URL, or ``None`` when disabled."""
        return self._resources.ui_url

    def initialize(
        self,
        *,
        schema_source: Mapping[str, object],
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ToolContext:
        """Parse and bind one target snapshot to this App.

        Args:
            schema_source: Closed file, URL, or inline OpenAPI source object.
            base_url: Optional target base URL used by request Tools.
            headers: Optional target headers copied into the Context.

        Returns:
            The newly bound Tool Context.

        Raises:
            ToolContextError: The App was already initialized.
            ValueError: Parsing reports errors or no testable operations.
            RuntimeError: The App was already closed.
        """
        self._ensure_open()
        if self.tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )

        source = TypeAdapter(_SchemaSource).validate_python(dict(schema_source))
        ir = OpenAPIParser.parse(_schema_source_value(source))
        parser_errors = [
            *ir.diagnostics.spec_errors,
            *ir.diagnostics.path_errors,
            *ir.diagnostics.operation_errors,
        ]
        if parser_errors:
            first = parser_errors[0]
            raise ValueError(
                f"OpenAPI parsing produced {len(parser_errors)} error(s): "
                f"{first.message}"
            )
        if not ir.operations:
            raise ValueError("OpenAPI schema contains no testable operations")

        context = ToolContext(
            ir=ir,
            baseline_schema_source=source.model_dump(mode="json"),
            base_url=base_url,
            headers=headers or {},
        )
        self._resources.bind_target(context)
        return context

    def export_current_openapi(self) -> dict[str, object]:
        """Return the normalized OpenAPI document persisted for audit/export."""
        self._ensure_open()
        return self._resources.current_openapi()

    def list_openapi_change_events(
        self,
        operation_key: str | None = None,
    ) -> list[OpenAPIChangeEventRecord]:
        """Return chronological persisted response changes for inspection."""
        self._ensure_open()
        return self._resources.list_openapi_changes(operation_key)

    def start(self) -> None:
        """Start the Main Agent once and block until its model loop finishes.

        The initialized target and stable Main Profile define the work. The
        method returns no result; cancellation, provider, budget, and prompt
        failures remain terminal exceptions.
        """
        self._ensure_open()
        if self.tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        if self._main_loop_started:
            raise RuntimeError("RESTScope Main Agent loop has already started")
        self._main_loop_started = True
        marker = {"profile_name": "main", "mode": "blocking"}
        observer = self._resources.run_observer
        if observer is not None:
            observer.begin_run(marker)
        try:
            with self.tracing_runtime.span(
                "RESTScopeApp.start",
                kind="CHAIN",
                input_value=marker,
            ) as span:
                main_agent = self._resources.start_main_agent()
                main_agent.start()
                terminal = {"profile_name": "main", "status": "completed"}
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
        """Close every adopted App resource once and clear target state."""
        if self._closed:
            return
        try:
            self._resources.close()
        finally:
            self._closed = True

    def __enter__(self) -> "RESTScopeApp":
        """Return this open App for context-managed use."""
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the App without suppressing the surrounding exception."""
        del exc_type, exc, tb
        self.close()

    def _ensure_open(self) -> None:
        """Reject lifecycle operations after the App has closed."""
        if self._closed:
            raise RuntimeError("RESTScopeApp is already closed")


def _schema_source_value(source: _SchemaSource) -> str:
    """Return the parser input represented by one validated source variant."""
    if source.kind == "file":
        return source.path
    if source.kind == "url":
        return source.url
    return source.content
