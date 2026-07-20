"""Top-level RESTScope application bootstrap API."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from restscope.agent import (
    LLMOperationDependencyAnalyzer,
    OperationDependencyAnalyzer,
    OperationTestRunner,
    RESTScopeMainGraph,
    RESTScopeRunReport,
    RESTScopeRunRequest,
    SchemathesisOperationRunner,
)
from restscope.capabilities import CapabilityRuntime, build_capabilities_with_mcp_host
from restscope.llm import ModelSelector, build_llm_client
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
    ) -> None:
        self.config = config
        self.operation_runner = operation_runner
        self.dependency_analyzer = dependency_analyzer
        self.capability_runtime = capability_runtime
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: str | Path | None = None,
        operation_runner: OperationTestRunner | None = None,
        dependency_analyzer: OperationDependencyAnalyzer | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
    ) -> "RESTScopeApp":
        """Load `.env`/environment config and build the program runtime."""

        config = RESTScopeConfig.from_environment(Path(env_file).expanduser() if env_file else None)
        return cls.from_config(
            config,
            operation_runner=operation_runner,
            dependency_analyzer=dependency_analyzer,
            capability_runtime=capability_runtime,
        )

    @classmethod
    def from_config(
        cls,
        config: RESTScopeConfig,
        *,
        operation_runner: OperationTestRunner | None = None,
        dependency_analyzer: OperationDependencyAnalyzer | None = None,
        capability_runtime: CapabilityRuntime | Any | None = None,
    ) -> "RESTScopeApp":
        """Build RESTScope from an explicit config object."""

        runtime = capability_runtime
        runner = operation_runner
        if runner is None:
            runtime = runtime or build_capabilities_with_mcp_host(config=config.mcp.servers_file)
            runner = SchemathesisOperationRunner(tool_executor=runtime.tool_executor)

        analyzer = dependency_analyzer
        if analyzer is None:
            selector = ModelSelector.from_config(config.llm)
            analyzer = LLMOperationDependencyAnalyzer(
                client=build_llm_client(config.llm),
                model=selector.select("operation_dependency_analyzer"),
            )

        return cls(
            config=config,
            operation_runner=runner,
            dependency_analyzer=analyzer,
            capability_runtime=runtime,
        )

    def run(self, request: RESTScopeRunRequest) -> RESTScopeRunReport:
        """Run the global RESTScope supervisor graph."""

        self._ensure_open()
        return RESTScopeMainGraph(
            operation_runner=self.operation_runner,
            dependency_analyzer=self.dependency_analyzer,
        ).run(request)

    def close(self) -> None:
        """Close owned runtime resources."""

        if self._closed:
            return
        mcp_host = getattr(self.capability_runtime, "mcp_host", None)
        if mcp_host is not None:
            mcp_host.close()
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
