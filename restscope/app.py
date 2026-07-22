"""Top-level RESTScope application bootstrap API."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

from pydantic import TypeAdapter

from restscope.agent import (
    LLMOperationDependencyAnalyzer,
    OperationDependencyAnalyzer,
    OperationTestRunner,
    RESTScopeMainGraph,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    SchemaSource,
    SchemathesisOperationRunner,
)
from restscope.capabilities import (
    CapabilityRuntime,
    ToolContext,
    ToolContextError,
    build_capabilities,
    build_capabilities_with_mcp_host,
)
from restscope.llm import ModelSelector, build_llm_client
from restscope.openapi_parser import OpenAPIParser
from restscope.observability import TracingRuntime, build_tracing_runtime
from restscope.restscope_config import RESTScopeConfig


class RESTScopeApp:
    """Program-level bootstrap object for running RESTScope graphs."""

    def __init__(
        self,
        *,
        config: RESTScopeConfig,
        operation_runner: OperationTestRunner,
        dependency_analyzer: OperationDependencyAnalyzer,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.config = config
        self.operation_runner = operation_runner
        self.dependency_analyzer = dependency_analyzer
        self._tracing_runtime = tracing_runtime or _build_app_tracing_runtime(config)
        self._tracing_runtime.register_secrets(
            (
                config.llm.thinking.api_key,
                config.llm.fast.api_key,
            )
        )
        self.capability_runtime = capability_runtime or build_capabilities(
            presets=(),
            tracing_runtime=self._tracing_runtime,
        )
        executor = getattr(self.capability_runtime, "tool_executor", None)
        if executor is not None:
            executor.tracing_runtime = self._tracing_runtime
        self._tool_context: ToolContext | None = None
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: str | Path | None = None,
        operation_runner: OperationTestRunner | None = None,
        dependency_analyzer: OperationDependencyAnalyzer | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Load `.env`/environment config and build the program runtime."""

        config = RESTScopeConfig.from_environment(Path(env_file).expanduser() if env_file else None)
        return cls.from_config(
            config,
            operation_runner=operation_runner,
            dependency_analyzer=dependency_analyzer,
            capability_runtime=capability_runtime,
            tracing_runtime=tracing_runtime,
        )

    @classmethod
    def from_config(
        cls,
        config: RESTScopeConfig,
        *,
        operation_runner: OperationTestRunner | None = None,
        dependency_analyzer: OperationDependencyAnalyzer | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> "RESTScopeApp":
        """Build RESTScope from an explicit config object."""

        trace_runtime = tracing_runtime or _build_app_tracing_runtime(config)
        runtime = capability_runtime
        runner = operation_runner
        if runner is None:
            runtime = runtime or build_capabilities_with_mcp_host(
                config=config.mcp.servers_file,
                tracing_runtime=trace_runtime,
            )
            runner = SchemathesisOperationRunner(tool_executor=runtime.tool_executor)
        elif runtime is None:
            runtime = build_capabilities(
                presets=(),
                tracing_runtime=trace_runtime,
            )

        analyzer = dependency_analyzer
        if analyzer is None:
            selector = ModelSelector.from_config(config.llm)
            analyzer = LLMOperationDependencyAnalyzer(
                client=build_llm_client(
                    config.llm,
                    tracing_runtime=trace_runtime,
                ),
                model=selector.select("operation_dependency_analyzer"),
            )

        return cls(
            config=config,
            operation_runner=runner,
            dependency_analyzer=analyzer,
            capability_runtime=runtime,
            tracing_runtime=trace_runtime,
        )

    @property
    def tool_context(self) -> ToolContext | None:
        return self._tool_context

    @property
    def tracing_runtime(self) -> TracingRuntime:
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
        self.tracing_runtime.register_secrets(context.headers.values())
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
                operation_runner=self.operation_runner,
                dependency_analyzer=self.dependency_analyzer,
                tool_context=self._tool_context,
                tracing_runtime=self.tracing_runtime,
            ).run(request)
            span.set_output(report)
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


def _schema_source_value(source: Any) -> str:
    if source.kind == "file":
        return source.path
    if source.kind == "url":
        return source.url
    return source.content


def _build_app_tracing_runtime(config: RESTScopeConfig) -> TracingRuntime:
    return build_tracing_runtime(
        config.tracing,
        secret_values=(
            config.llm.thinking.api_key,
            config.llm.fast.api_key,
        ),
    )
