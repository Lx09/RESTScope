"""Top-level RESTScope application bootstrap API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import TypeAdapter

from restscope.agent import (
    OperationSmokeAgent,
    RESTScopeMainGraph,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    APIBehaviorResponseProcessor,
    BehaviorMonitorReferenceValues,
    SchemaSource,
    build_api_behavior_monitor_agent,
    build_operation_smoke_agent,
)
from restscope.capabilities import (
    CapabilityRuntime,
    ToolContext,
    ToolContextError,
    build_capabilities,
)
from restscope.http_transport import TargetHTTPTransport
from restscope.openapi_parser import OpenAPIParser
from restscope.observability import TracingRuntime, build_tracing_runtime
from restscope.redaction import Redactor
from restscope.restscope_config import RESTScopeConfig
from restscope.bootstrap import build_generator_config_catalog
from restscope.db.bootstrap import _FreshSQLiteDatabase, prepare_fresh_sqlite
from restscope.testing import OperationTestingService


class RESTScopeApp:
    """Own all long-lived services used by one RESTScope process.

    Creating the app is intentionally more than constructing a data object.  It
    opens the run-local database, tracing exporter, behavior monitor, HTTP
    transport, testing service, capability registry, and top-level Agent graph.
    The app also records which resources it created so :meth:`close` can release
    them if startup succeeds *or* fails part-way through.
    """

    def __init__(
        self,
        *,
        config: RESTScopeConfig,
        operation_smoke_agent: OperationSmokeAgent | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Build the dependency graph, or adopt explicitly injected test doubles.

        ``capability_runtime`` and ``operation_smoke_agent`` are injection
        points used by tests and embedders.  In the normal path both are
        omitted and RESTScope wires the complete production stack itself.
        """

        # Keep local ownership markers until construction completes.  If any
        # later constructor raises, the exception handler can close only the
        # resources opened by this method and leave injected objects untouched.
        database: _FreshSQLiteDatabase | None = None
        built_runtime: CapabilityRuntime | Any | None = None
        built_tracing_runtime = tracing_runtime is None
        trace_runtime: TracingRuntime | None = None
        try:
            # A default runtime needs a private, freshly migrated SQLite file.
            # An injected runtime owns its own persistence and must not be
            # silently paired with another database.
            if capability_runtime is None:
                config, database = _prepare_app_database(config)

            self.config = config
            smoke_agent = operation_smoke_agent
            trace_runtime = (
                _build_app_tracing_runtime(config)
                if tracing_runtime is None
                else tracing_runtime
            )
            self._tracing_runtime = trace_runtime
            self._tracing_runtime.redactor.register_secrets(
                (
                    config.llm.thinking.api_key,
                    config.llm.fast.api_key,
                    config.tracing.api_key,
                )
            )
            if capability_runtime is None:
                # The catalog stores durable generator revisions.  The behavior
                # monitor supplies observed identifiers/response values to
                # generators, while the transport sends requests and returns
                # every response to that monitor.
                generator_catalog = build_generator_config_catalog(config)
                api_behavior_monitor_agent = build_api_behavior_monitor_agent(
                    config,
                    tracing_runtime=self._tracing_runtime,
                )
                reference_values = BehaviorMonitorReferenceValues(
                    api_behavior_monitor_agent
                )
                target_transport = TargetHTTPTransport(
                    response_processor=APIBehaviorResponseProcessor(
                        api_behavior_monitor_agent
                    )
                )
                operation_testing_service = OperationTestingService(
                    config_catalog=generator_catalog,
                    transport=target_transport,
                    tracing_runtime=self._tracing_runtime,
                    reference_values=reference_values,
                )
                # Capabilities expose these concrete services as policy-checked
                # tools.  Agents receive the runtime instead of importing
                # database or network implementations directly.
                built_runtime = build_capabilities(
                    tracing_runtime=self._tracing_runtime,
                    generator_config_catalog=generator_catalog,
                    operation_testing_service=operation_testing_service,
                    target_http_transport=target_transport,
                    api_behavior_monitor_agent=api_behavior_monitor_agent,
                )
                capability_runtime = built_runtime
                if smoke_agent is None:
                    smoke_agent = build_operation_smoke_agent(
                        config,
                        config_catalog=generator_catalog,
                        batch_runner=operation_testing_service,
                        reference_values=reference_values,
                        tool_executor=built_runtime.tool_executor,
                        tracing_runtime=self._tracing_runtime,
                    )
            elif smoke_agent is None:
                # Mixing a custom tool runtime with a default Smoke Agent would
                # connect two unrelated dependency graphs, so require callers
                # to inject the matching Agent explicitly.
                raise ValueError(
                    "A custom capability runtime requires an injected "
                    "OperationSmokeAgent"
                )
            self.operation_smoke_agent = smoke_agent
            self.capability_runtime = capability_runtime
            bind_tracing_runtime = getattr(
                self.capability_runtime,
                "bind_tracing_runtime",
                None,
            )
            if callable(bind_tracing_runtime):
                bind_tracing_runtime(self._tracing_runtime)
            else:
                # Compatibility path for small test doubles that predate the
                # runtime-level binding method.
                executor = getattr(self.capability_runtime, "tool_executor", None)
                if executor is not None:
                    executor.tracing_runtime = self._tracing_runtime
            self._tool_context: ToolContext | None = None
            self._closed = False
        except BaseException:
            # Startup is transactional from the caller's perspective: a failed
            # constructor must not leave an MCP host, exporter, or temporary
            # database running in the background.
            _close_runtime_host(built_runtime)
            if built_tracing_runtime and trace_runtime is not None:
                trace_runtime.close()
            if database is not None:
                database.cleanup()
            raise

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: str | Path | None = None,
        operation_smoke_agent: OperationSmokeAgent | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Load `.env`/environment config and build the program runtime."""

        config = RESTScopeConfig.from_environment(Path(env_file).expanduser() if env_file else None)
        return cls.from_config(
            config,
            operation_smoke_agent=operation_smoke_agent,
            capability_runtime=capability_runtime,
            tracing_runtime=tracing_runtime,
        )

    @classmethod
    def from_config(
        cls,
        config: RESTScopeConfig,
        *,
        operation_smoke_agent: OperationSmokeAgent | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Build RESTScope from an explicit config object."""

        database: _FreshSQLiteDatabase | None = None
        trace_runtime: TracingRuntime | None = None
        runtime = capability_runtime
        runtime_is_owned = False
        try:
            if runtime is None:
                config, database = _prepare_app_database(config)

            trace_runtime = (
                _build_app_tracing_runtime(config)
                if tracing_runtime is None
                else tracing_runtime
            )
            smoke_agent = operation_smoke_agent
            generator_catalog = None
            operation_testing_service = None
            api_behavior_monitor_agent = None
            target_transport = None
            reference_values = None
            if runtime is None:
                generator_catalog = build_generator_config_catalog(config)
                api_behavior_monitor_agent = build_api_behavior_monitor_agent(
                    config,
                    tracing_runtime=trace_runtime,
                )
                reference_values = BehaviorMonitorReferenceValues(
                    api_behavior_monitor_agent
                )
                target_transport = TargetHTTPTransport(
                    response_processor=APIBehaviorResponseProcessor(
                        api_behavior_monitor_agent
                    )
                )
                operation_testing_service = OperationTestingService(
                    config_catalog=generator_catalog,
                    transport=target_transport,
                    tracing_runtime=trace_runtime,
                    reference_values=reference_values,
                )
                assert generator_catalog is not None
                assert operation_testing_service is not None
                assert api_behavior_monitor_agent is not None
                runtime = build_capabilities(
                    tracing_runtime=trace_runtime,
                    generator_config_catalog=generator_catalog,
                    operation_testing_service=operation_testing_service,
                    target_http_transport=target_transport,
                    api_behavior_monitor_agent=api_behavior_monitor_agent,
                )
                runtime_is_owned = True
            elif smoke_agent is None:
                operation_testing_service = getattr(
                    runtime,
                    "operation_testing_service",
                    None,
                )
                api_behavior_monitor_agent = getattr(
                    runtime,
                    "api_behavior_monitor_agent",
                    None,
                )
                if (
                    operation_testing_service is None
                    or api_behavior_monitor_agent is None
                ):
                    raise ValueError(
                        "A custom capability runtime requires an injected "
                        "OperationSmokeAgent or testing and API behavior "
                        "monitor services"
                    )
                generator_catalog = operation_testing_service.config_catalog
                reference_values = (
                    operation_testing_service.reference_values
                    or BehaviorMonitorReferenceValues(
                        api_behavior_monitor_agent
                    )
                )
                operation_testing_service.reference_values = reference_values

            if smoke_agent is None:
                assert generator_catalog is not None
                assert operation_testing_service is not None
                assert reference_values is not None
                smoke_agent = build_operation_smoke_agent(
                    config,
                    config_catalog=generator_catalog,
                    batch_runner=operation_testing_service,
                    reference_values=reference_values,
                    tool_executor=runtime.tool_executor,
                    tracing_runtime=trace_runtime,
                )

            return cls(
                config=config,
                operation_smoke_agent=smoke_agent,
                capability_runtime=runtime,
                tracing_runtime=trace_runtime,
            )
        except BaseException:
            if runtime_is_owned:
                _close_runtime_host(runtime)
            if tracing_runtime is None and trace_runtime is not None:
                trace_runtime.close()
            if database is not None:
                database.cleanup()
            raise

    @property
    def tool_context(self) -> ToolContext | None:
        """
        Handle tool context as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._tool_context

    @property
    def tracing_runtime(self) -> TracingRuntime:
        """
        Handle tracing runtime as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._tracing_runtime

    def initialize(
        self,
        *,
        schema_source: Mapping[str, Any],
        base_url: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ToolContext:
        """Parse and bind one immutable-by-convention target snapshot to this App."""

        self._ensure_open()
        if self._tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )

        source = TypeAdapter(SchemaSource).validate_python(dict(schema_source))
        ir = OpenAPIParser.parse(_schema_source_value(source))
        parser_errors = [
            *ir.diagnostics.spec_errors,
            *ir.diagnostics.path_errors,
            *ir.diagnostics.operation_errors,
        ]
        if parser_errors:
            first = parser_errors[0]
            raise ValueError(f"OpenAPI parsing produced {len(parser_errors)} error(s): {first.message}")
        if not ir.operations:
            raise ValueError("OpenAPI schema contains no testable operations")

        context = ToolContext(
            ir=ir,
            baseline_schema_source=source.model_dump(mode="json"),
            base_url=base_url,
            headers=headers or {},
        )
        testing_service = getattr(
            self.capability_runtime,
            "operation_testing_service",
            None,
        )
        if testing_service is not None:
            testing_service.config_catalog.initialize_once(ir)
        self.capability_runtime.tool_executor.bind_context(context)
        self._tool_context = context
        return context

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        """Run the global RESTScope supervisor graph."""

        self._ensure_open()
        if self._tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        task_id = request.metadata.get("task_id")
        attributes = {"restscope.task_id": task_id} if task_id else {}
        with self.tracing_runtime.span(
            "RESTScopeApp.run",
            kind="CHAIN",
            input_value=request,
            attributes=attributes,
        ) as span:
            report = RESTScopeMainGraph(
                operation_smoke_agent=self.operation_smoke_agent,
                tool_context=self._tool_context,
                tracing_runtime=self.tracing_runtime,
            ).run(request)
            span.set_output(_app_run_trace_summary(report))
            span.set_attribute("restscope.run.status", report.status)
            if report.status == "errored":
                span.mark_error("RESTScope run returned an errored report")
            return report

    def close(self) -> None:
        """Close owned runtime resources."""

        if self._closed:
            return
        executor = getattr(self.capability_runtime, "tool_executor", None)
        if executor is not None:
            executor.clear_context()
        self._tool_context = None
        clear_smoke_state = getattr(
            self.operation_smoke_agent,
            "clear_app_state",
            None,
        )
        if callable(clear_smoke_state):
            try:
                clear_smoke_state()
            except Exception:
                # Resource cleanup must continue even if a custom injected
                # Smoke Agent cannot release its optional in-memory state.
                pass
        mcp_host = getattr(self.capability_runtime, "mcp_host", None)
        try:
            if mcp_host is not None:
                mcp_host.close()
        finally:
            self.tracing_runtime.close()
            self._closed = True

    def __enter__(self) -> "RESTScopeApp":
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RESTScopeApp is already closed")


def _app_run_trace_summary(report: RESTScopeRunReport) -> dict[str, object]:
    return {
        "report_id": report.report_id,
        "status": report.status,
        "stop_reason": report.stop_reason,
        "operation_count": len(report.operations),
        "attempt_count": report.attempt_count,
    }


def _schema_source_value(source: Any) -> str:
    if source.kind == "file":
        return source.path
    if source.kind == "url":
        return source.url
    return source.content


def _build_app_tracing_runtime(config: RESTScopeConfig) -> TracingRuntime:
    return build_tracing_runtime(
        config.tracing,
        redactor=Redactor(
            (
                config.llm.thinking.api_key,
                config.llm.fast.api_key,
                config.tracing.api_key,
            )
        ),
    )


def _prepare_app_database(
    config: RESTScopeConfig,
) -> tuple[RESTScopeConfig, _FreshSQLiteDatabase]:
    """Prepare the default runtime's one-shot database and normalize its config."""

    db_config, database = prepare_fresh_sqlite(config.db)
    return replace(config, db=db_config), database


def _close_runtime_host(runtime: Any | None) -> None:
    if runtime is None:
        return
    host = getattr(runtime, "mcp_host", None)
    if host is not None:
        try:
            host.close()
        except Exception:
            pass
