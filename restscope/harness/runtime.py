"""Construct the deterministic Harness and its explicit Agent access Catalogs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

from restscope.agent import Agent, SystemAgentResult
from restscope.observability import TracingRuntime
from restscope.openapi_parser.ir import OperationIR
from restscope.target_api import TargetAPIClient
from restscope.tools import (
    ToolCatalog,
    ToolDefinition,
    builtin_tool_catalog,
)
from restscope.tools.context import ToolContext, ToolContextError
from restscope.tools.external import register_tool_source
from restscope.tools.external.mcp import (
    MCPHost,
    MCPServerConfig,
    MCPSourceBuilder,
    load_mcp_server_configs,
)
from restscope.tools.http import HTTP_REQUEST_TOOL_NAME, TargetHTTPRequestTool
from restscope.tools.openapi import (
    ObservedResponseReader,
    OpenAPIToolBackend,
    openapi_tool_bindings,
)
from restscope.tools.resource import ResourceToolBackend, resource_tool_bindings
from restscope.tools.runtime import AgentToolbox, ToolBinding

from .agent_runtime import (
    AgentRuntimeDefinition,
    AgentRuntimeResolver,
    ToolBindingFactory,
)

if TYPE_CHECKING:
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.request_generation import RequestGenerationPatchRuntime
    from restscope.tools.test_case import TestCaseQueryToolBackend


class SystemAgentNotConfiguredError(RuntimeError):
    """Report an unknown or unavailable Harness-owned System Agent Profile."""

    code = "system_agent_not_configured"


@dataclass
class HarnessRuntime:
    """Own deterministic state and run fully authorized System Agents.

    The built-in Catalog is globally discoverable but grants no execution
    permission. External MCP definitions remain in a separate Catalog. Live
    implementations and App-bound context stay inside the Harness.
    """

    http_request_tool: TargetHTTPRequestTool
    built_in_tool_catalog: ToolCatalog = field(default_factory=builtin_tool_catalog)
    external_tool_catalog: ToolCatalog = field(default_factory=ToolCatalog)
    external_tools: AgentToolbox | None = None
    mcp_host: MCPHost | None = None
    agent_runtime: AgentRuntimeResolver | None = None
    observed_response_reader: ObservedResponseReader | None = field(
        default=None,
        repr=False,
    )
    resource_tool_backend: ResourceToolBackend | None = None
    openapi_backend: OpenAPIToolBackend = field(init=False)
    _tool_context: ToolContext | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _system_agents: dict[str, Agent] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _system_agents_lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _system_agents_closing: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Bind the OpenAPI Tool backend to this Harness's target context."""
        self.openapi_backend = OpenAPIToolBackend(
            context_provider=self.require_context,
            observed_response_reader=self.observed_response_reader,
        )

    def bind_tracing_runtime(self, tracing_runtime: TracingRuntime) -> None:
        """Bind one tracing/redaction runtime to every built-in trace consumer."""

        if self.external_tools is not None:
            self.external_tools.tracing_runtime = tracing_runtime

    def bind_context(self, context: ToolContext) -> None:
        """Bind target and OpenAPI state once after successful App parsing."""
        if self._tool_context is not None:
            raise ToolContextError(
                "tool_context_already_initialized",
                "Tool context is already initialized",
            )
        self._tool_context = context

    def require_context(self) -> ToolContext:
        """Return initialized target state or report stable startup misuse."""
        if self._tool_context is None:
            raise ToolContextError(
                "tool_context_not_initialized",
                "Tool context is not initialized",
            )
        return self._tool_context

    def require_operation(self, operation_key: str) -> OperationIR:
        """Return one exact operation from the currently bound OpenAPI IR."""
        try:
            return self.require_context().ir.operations[operation_key]
        except KeyError as exc:
            raise LookupError(
                f"OpenAPI operation was not found: {operation_key}"
            ) from exc

    def clear_context(self) -> None:
        """Release App-bound target state during shutdown."""
        self._tool_context = None

    def run_system_agent(
        self,
        profile_name: str,
        task: object,
    ) -> SystemAgentResult:
        """Run one independent registered System Agent synchronously.

        Every invocation owns a fresh root tree and Profile-resolved capability
        set. The Harness tracks it only while active so App shutdown can request
        cancellation even when unlimited result correction is still running.
        """
        if self.agent_runtime is None:
            raise SystemAgentNotConfiguredError(
                "Generic Agent runtime is not configured"
            )
        try:
            agent, bounded_task = self.agent_runtime.start_system(profile_name, task)
        except (KeyError, ValueError) as exc:
            raise SystemAgentNotConfiguredError(str(exc)) from exc
        with self._system_agents_lock:
            if self._system_agents_closing:
                agent.close()
                raise RuntimeError("Harness is closing System Agents")
            self._system_agents[agent.session_id] = agent
        try:
            result = agent.run(bounded_task)
            if not isinstance(result, SystemAgentResult):
                raise TypeError("System Agent returned the wrong lifecycle result")
            return result
        finally:
            agent.close()
            with self._system_agents_lock:
                self._system_agents.pop(agent.session_id, None)

    def close_agents(self) -> None:
        """Cancel every active System root and all of its descendants."""
        with self._system_agents_lock:
            self._system_agents_closing = True
            active_system_agents = tuple(self._system_agents.values())
        for agent in active_system_agents:
            agent.close()


def build_harness(
    *,
    sources: Mapping[str, Mapping[str, object]] | None = None,
    tracing_runtime: TracingRuntime | None = None,
    target_api_client: TargetAPIClient | None = None,
    observed_response_reader: ObservedResponseReader | None = None,
    resource_tool_backend: ResourceToolBackend | None = None,
    request_generation_patch_runtime: RequestGenerationPatchRuntime | None = None,
    operation_testing_service: OperationTestingService | None = None,
    test_case_query_backend: TestCaseQueryToolBackend | None = None,
    agent_runtime: AgentRuntimeDefinition | None = None,
) -> HarnessRuntime:
    """Build shared App implementations and optional explicit integrations.

    Local OpenAPI and HTTP code is reusable but is not automatically
    model-visible here. ``sources`` creates an isolated caller-owned toolbox,
    Domain services remain App-owned; only their narrow Tool-facing readers may
    enter the Harness.
    """

    runtime_tracing = tracing_runtime or TracingRuntime.disabled()
    external_tools = (
        AgentToolbox(tracing_runtime=runtime_tracing)
        if sources
        else None
    )
    external_definitions: list[ToolDefinition] = []
    for server_name, source in (sources or {}).items():
        assert external_tools is not None
        specs = register_tool_source(
            toolbox=external_tools,
            server_name=server_name,
            source=source,
        )
        external_definitions.extend(
            ToolDefinition(subject="external", spec=spec) for spec in specs
        )

    runtime = HarnessRuntime(
        http_request_tool=TargetHTTPRequestTool(
            client=target_api_client,
        ),
        built_in_tool_catalog=builtin_tool_catalog(),
        external_tool_catalog=ToolCatalog(external_definitions),
        external_tools=external_tools,
        observed_response_reader=observed_response_reader,
        resource_tool_backend=resource_tool_backend,
    )
    if agent_runtime is not None:
        production_factories = _production_tool_binding_factories(
            runtime,
            include_http=target_api_client is not None,
            include_openapi=any(
                item is not None
                for item in (
                    observed_response_reader,
                    resource_tool_backend,
                    request_generation_patch_runtime,
                    operation_testing_service,
                    test_case_query_backend,
                )
            ),
            request_generation_patch_runtime=request_generation_patch_runtime,
            operation_testing_service=operation_testing_service,
            test_case_query_backend=test_case_query_backend,
        )
        agent_runtime = replace(
            agent_runtime,
            tool_binding_factories=(
                *agent_runtime.tool_binding_factories,
                *production_factories,
            ),
        )
        runtime.agent_runtime = AgentRuntimeResolver(
            agent_runtime,
            built_in_catalog=runtime.built_in_tool_catalog,
            external_catalog=runtime.external_tool_catalog,
            tracing_runtime=runtime_tracing,
        )
    return runtime


def _production_tool_binding_factories(
    runtime: HarnessRuntime,
    *,
    include_http: bool,
    include_openapi: bool,
    request_generation_patch_runtime: RequestGenerationPatchRuntime | None,
    operation_testing_service: OperationTestingService | None,
    test_case_query_backend: TestCaseQueryToolBackend | None,
) -> tuple[ToolBindingFactory, ...]:
    """Create implementations for every App-owned built-in domain Tool.

    Factories make Tools executable when a Profile grants them; they do not add
    names to any Profile and therefore confer no model permission by themselves.
    """
    bindings: list[ToolBinding] = []
    if include_http:
        bindings.append(
            ToolBinding(
                name=HTTP_REQUEST_TOOL_NAME,
                execute=lambda **arguments: runtime.http_request_tool.execute(
                    runtime.require_context(),
                    **arguments,
                ),
            )
        )
    if include_openapi:
        bindings.extend(openapi_tool_bindings(runtime.openapi_backend))
    if runtime.resource_tool_backend is not None:
        bindings.extend(
            resource_tool_bindings(
                runtime.resource_tool_backend,
                unavailable=_unavailable_tool,
            )
        )
    if request_generation_patch_runtime is not None:
        from restscope.tools.parameter_patch import (
            ParameterPatchApplyBackend,
            parameter_patch_apply_tool_binding,
        )
        from restscope.tools.request_generation import (
            RequestGenerationToolBackend,
            request_generation_tool_bindings,
        )

        bindings.extend(
            request_generation_tool_bindings(
                RequestGenerationToolBackend(request_generation_patch_runtime)
            )
        )
        bindings.append(
            parameter_patch_apply_tool_binding(
                ParameterPatchApplyBackend(request_generation_patch_runtime)
            )
        )
    if operation_testing_service is not None:
        from restscope.tools.test_case import (
            TestCaseBatchToolBackend,
            test_case_run_batch_tool_binding,
        )

        bindings.append(
            test_case_run_batch_tool_binding(
                TestCaseBatchToolBackend(
                    service=operation_testing_service,
                    context_provider=runtime.require_context,
                )
            )
        )
    if test_case_query_backend is not None:
        from restscope.tools.test_case import test_case_query_tool_bindings

        bindings.extend(test_case_query_tool_bindings(test_case_query_backend))
    return tuple(
        ToolBindingFactory(name=binding.name, create=lambda item=binding: item)
        for binding in bindings
    )


def _unavailable_tool(**_arguments: object) -> dict[str, object]:
    """Fail closed when an optional production Tool backend is absent."""
    from restscope.tools import ToolFailure

    raise ToolFailure(
        code="tool_backend_unavailable",
        message="The Tool backend is unavailable in this App runtime",
    )


def build_harness_with_mcp_host(
    *,
    config: Mapping[str, MCPServerConfig] | str | Path | None = None,
    mcp_host: MCPHost | None = None,
    server_names: Iterable[str] | None = None,
    tracing_runtime: TracingRuntime | None = None,
    target_api_client: TargetAPIClient | None = None,
    observed_response_reader: ObservedResponseReader | None = None,
    resource_tool_backend: ResourceToolBackend | None = None,
    request_generation_patch_runtime: RequestGenerationPatchRuntime | None = None,
    operation_testing_service: OperationTestingService | None = None,
    test_case_query_backend: TestCaseQueryToolBackend | None = None,
    agent_runtime: AgentRuntimeDefinition | None = None,
) -> HarnessRuntime:
    """Discover selected MCP servers into the isolated external toolbox.

    RESTScope owns and closes a Host only when this function constructs it.
    Passing ``mcp_host`` leaves that lifecycle with the caller. Discovered
    tools are never injected into a production Agent automatically.
    """

    owns_host = mcp_host is None
    host = MCPHost(_load_mcp_configs(config)) if mcp_host is None else mcp_host
    try:
        selected_names = (
            tuple(server_names) if server_names is not None else None
        )
        sources = MCPSourceBuilder(host).build_sources(
            server_names=selected_names
        )
        runtime = build_harness(
            sources=sources,
            tracing_runtime=tracing_runtime,
            target_api_client=target_api_client,
            observed_response_reader=observed_response_reader,
            resource_tool_backend=resource_tool_backend,
            request_generation_patch_runtime=request_generation_patch_runtime,
            operation_testing_service=operation_testing_service,
            test_case_query_backend=test_case_query_backend,
            agent_runtime=agent_runtime,
        )
        runtime.mcp_host = host
        return runtime
    except BaseException:
        if owns_host:
            try:
                host.close()
            except Exception:  # noqa: BLE001, S110
                pass
        raise


def _load_mcp_configs(
    config: Mapping[str, MCPServerConfig] | str | Path | None,
) -> dict[str, MCPServerConfig]:
    """Normalize default, file-backed, or already parsed MCP configuration."""
    if config is None:
        return load_mcp_server_configs()
    if isinstance(config, str | Path):
        return load_mcp_server_configs(config)
    return dict(config)
