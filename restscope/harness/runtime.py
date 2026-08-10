"""Construct the deterministic Harness and its explicit Agent access Catalogs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from restscope.agent import Agent

from restscope.tools.external.mcp import (
    MCPHost,
    MCPServerConfig,
    MCPSourceBuilder,
    load_mcp_server_configs,
)
from restscope.tools.runtime import AgentToolbox, ToolBinding
from restscope.tools.http import HTTP_REQUEST_TOOL_NAME, TargetHTTPRequestTool
from restscope.tools.openapi import OpenAPIToolBackend, openapi_tool_bindings
from restscope.tools.resource import ResourceToolBackend, resource_tool_bindings
from restscope.tools.context import ToolContext, ToolContextError
from restscope.tools.external import register_tool_source
from restscope.tools import (
    ToolCatalog,
    ToolDefinition,
    builtin_tool_catalog,
)
from restscope.target_http import TargetHTTPTransport
from restscope.observability import TracingRuntime
from restscope.openapi_parser.ir import OperationIR

from .agent_runtime import AgentRuntimeDefinition, AgentRuntimeResolver
from .agent_runtime import ToolBindingFactory

if TYPE_CHECKING:
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.request_generation import RequestGenerationPatchRuntime


class AgentRuntimeNotConfiguredError(RuntimeError):
    """Report that this Harness has no generic Agent composition definition."""

    code = "agent_runtime_not_configured"


@dataclass
class HarnessRuntime:
    """Own deterministic state and start one fully authorized Main Agent.

    The built-in Catalog is globally discoverable but grants no execution
    permission. External MCP definitions remain in a separate Catalog. Live
    implementations and App-bound context stay inside the Harness.
    """

    target_http_tool: TargetHTTPRequestTool
    built_in_tool_catalog: ToolCatalog = field(default_factory=builtin_tool_catalog)
    external_tool_catalog: ToolCatalog = field(default_factory=ToolCatalog)
    external_tools: AgentToolbox | None = None
    mcp_host: MCPHost | None = None
    agent_runtime: AgentRuntimeResolver | None = None
    observed_response_fields_provider: Callable[..., Any] | None = field(
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
    _main_agent: Agent | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        """Bind the OpenAPI Tool backend to this Harness's target context."""
        self.openapi_backend = OpenAPIToolBackend(
            context_provider=self.require_context,
            observed_response_fields_provider=self.observed_response_fields_provider,
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

    def start_main_agent(self, profile_name: str) -> Agent:
        """Resolve and start the App's one long-lived generic Main Agent."""
        if self.agent_runtime is None:
            raise AgentRuntimeNotConfiguredError(
                "Generic Agent runtime is not configured"
            )
        if self._main_agent is not None and not self._main_agent.closed:
            raise ValueError("Main Agent is already started")
        self._main_agent = self.agent_runtime.start(profile_name)
        return self._main_agent

    def close_main_agent(self) -> None:
        """Close the current Main Agent and cooperatively cancel its descendants."""
        if self._main_agent is not None:
            self._main_agent.close()
            self._main_agent = None

def build_harness(
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    tracing_runtime: TracingRuntime | None = None,
    target_http_transport: TargetHTTPTransport | None = None,
    observed_response_fields_provider: Callable[..., Any] | None = None,
    resource_tool_backend: ResourceToolBackend | None = None,
    request_generation_patch_runtime: "RequestGenerationPatchRuntime | None" = None,
    operation_testing_service: "OperationTestingService | None" = None,
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
        target_http_tool=TargetHTTPRequestTool(
            transport=target_http_transport,
        ),
        built_in_tool_catalog=builtin_tool_catalog(),
        external_tool_catalog=ToolCatalog(external_definitions),
        external_tools=external_tools,
        observed_response_fields_provider=observed_response_fields_provider,
        resource_tool_backend=resource_tool_backend,
    )
    if agent_runtime is not None:
        production_factories = _production_tool_binding_factories(
            runtime,
            include_http=target_http_transport is not None,
            include_openapi=any(
                item is not None
                for item in (
                    observed_response_fields_provider,
                    resource_tool_backend,
                    request_generation_patch_runtime,
                    operation_testing_service,
                )
            ),
            request_generation_patch_runtime=request_generation_patch_runtime,
            operation_testing_service=operation_testing_service,
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
    request_generation_patch_runtime: "RequestGenerationPatchRuntime | None",
    operation_testing_service: "OperationTestingService | None",
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
                execute=lambda **arguments: runtime.target_http_tool.execute(
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
    return tuple(
        ToolBindingFactory(name=binding.name, create=lambda item=binding: item)
        for binding in bindings
    )


def _unavailable_tool(**_arguments: Any) -> dict[str, Any]:
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
    target_http_transport: TargetHTTPTransport | None = None,
    observed_response_fields_provider: Callable[..., Any] | None = None,
    resource_tool_backend: ResourceToolBackend | None = None,
    request_generation_patch_runtime: "RequestGenerationPatchRuntime | None" = None,
    operation_testing_service: "OperationTestingService | None" = None,
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
            target_http_transport=target_http_transport,
            observed_response_fields_provider=observed_response_fields_provider,
            resource_tool_backend=resource_tool_backend,
            request_generation_patch_runtime=request_generation_patch_runtime,
            operation_testing_service=operation_testing_service,
            agent_runtime=agent_runtime,
        )
        runtime.mcp_host = host
        return runtime
    except BaseException:
        if owns_host:
            try:
                host.close()
            except Exception:
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
