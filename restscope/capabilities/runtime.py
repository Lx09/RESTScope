"""One-call construction for RESTScope capability runtime objects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from restscope.capabilities.mcp import MCPHost, MCPServerConfig, MCPSourceBuilder, load_mcp_server_configs
from restscope.capabilities.http_request import register_http_request_tool
from restscope.capabilities.testing_tools import register_testing_tools
from restscope.capabilities.skills import SkillManifest, SkillPolicy, SkillRegistry
from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_executor import ToolExecutor
from restscope.capabilities.tool_policy import ToolPolicy
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.capabilities.tool_selector import ToolSelector
from restscope.capabilities.tool_sources import register_tool_source
from restscope.http_transport import TargetHTTPTransport
from restscope.observability import TracingRuntime
from restscope.testing import GeneratorConfigCatalog, OperationTestingService


@dataclass(frozen=True)
class CapabilityRuntime:
    """Runtime bundle for tools and prompt-only skill metadata."""

    tool_registry: ToolRegistry
    tool_policy: ToolPolicy
    tool_selector: ToolSelector
    tool_executor: ToolExecutor
    skill_registry: SkillRegistry
    skill_policy: SkillPolicy
    mcp_host: MCPHost | None = None
    operation_testing_service: OperationTestingService | None = None
    api_behavior_monitor_agent: Any | None = None

    def bind_tracing_runtime(self, tracing_runtime: TracingRuntime) -> None:
        """Bind one tracing/redaction runtime to every built-in trace consumer."""

        self.tool_executor.tracing_runtime = tracing_runtime
        if self.operation_testing_service is not None:
            self.operation_testing_service.tracing_runtime = tracing_runtime
        if self.api_behavior_monitor_agent is not None:
            self.api_behavior_monitor_agent.tracing_runtime = tracing_runtime
            self.api_behavior_monitor_agent.resource_identifier_tracker.tracing_runtime = (
                tracing_runtime
            )
            client = getattr(self.api_behavior_monitor_agent, "client", None)
            if client is not None and hasattr(client, "tracing_runtime"):
                client.tracing_runtime = tracing_runtime


def build_capabilities(
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    skills: Iterable[SkillManifest] = (),
    tracing_runtime: TracingRuntime | None = None,
    generator_config_catalog: GeneratorConfigCatalog | None = None,
    operation_testing_service: OperationTestingService | None = None,
    target_http_transport: TargetHTTPTransport | None = None,
    api_behavior_monitor_agent: Any | None = None,
) -> CapabilityRuntime:
    """Build a complete capability runtime from external sources and skills."""

    tool_registry = ToolRegistry()
    tool_policy = ToolPolicy()
    tool_selector = ToolSelector(tool_registry, tool_policy)
    tool_validator = ToolCallValidator(tool_registry, tool_policy)
    tool_executor = ToolExecutor(
        tool_registry,
        tool_validator,
        tracing_runtime=tracing_runtime,
    )
    skill_registry = SkillRegistry()
    skill_policy = SkillPolicy()

    register_http_request_tool(
        tool_registry,
        transport=target_http_transport,
    )
    if api_behavior_monitor_agent is not None:
        from restscope.agent.api_behavior_monitor import register_resource_lookup_tool

        register_resource_lookup_tool(tool_registry, api_behavior_monitor_agent)
    if (generator_config_catalog is None) != (operation_testing_service is None):
        raise ValueError(
            "generator_config_catalog and operation_testing_service must be supplied together"
        )
    if generator_config_catalog is not None and operation_testing_service is not None:
        register_testing_tools(
            tool_registry,
            generator_config_catalog=generator_config_catalog,
            operation_testing_service=operation_testing_service,
        )

    for skill in skills:
        skill_registry.register(skill)

    for server_name, source in (sources or {}).items():
        register_tool_source(
            registry=tool_registry,
            server_name=server_name,
            source=source,
        )

    return CapabilityRuntime(
        tool_registry=tool_registry,
        tool_policy=tool_policy,
        tool_selector=tool_selector,
        tool_executor=tool_executor,
        skill_registry=skill_registry,
        skill_policy=skill_policy,
        operation_testing_service=operation_testing_service,
        api_behavior_monitor_agent=api_behavior_monitor_agent,
    )


def build_capabilities_with_mcp_host(
    *,
    config: Mapping[str, MCPServerConfig] | str | Path | None = None,
    mcp_host: MCPHost | None = None,
    server_names: Iterable[str] | None = None,
    skills: Iterable[SkillManifest] = (),
    tracing_runtime: TracingRuntime | None = None,
    generator_config_catalog: GeneratorConfigCatalog | None = None,
    operation_testing_service: OperationTestingService | None = None,
    target_http_transport: TargetHTTPTransport | None = None,
    api_behavior_monitor_agent: Any | None = None,
) -> CapabilityRuntime:
    """Build capabilities after discovering tools through RESTScope's MCP host."""

    owns_host = mcp_host is None
    host = MCPHost(_load_mcp_configs(config)) if mcp_host is None else mcp_host
    try:
        selected_names = (
            tuple(server_names) if server_names is not None else None
        )
        sources = MCPSourceBuilder(host).build_sources(
            server_names=selected_names
        )
        runtime = build_capabilities(
            sources=sources,
            skills=skills,
            tracing_runtime=tracing_runtime,
            generator_config_catalog=generator_config_catalog,
            operation_testing_service=operation_testing_service,
            target_http_transport=target_http_transport,
            api_behavior_monitor_agent=api_behavior_monitor_agent,
        )
        return CapabilityRuntime(
            tool_registry=runtime.tool_registry,
            tool_policy=runtime.tool_policy,
            tool_selector=runtime.tool_selector,
            tool_executor=runtime.tool_executor,
            skill_registry=runtime.skill_registry,
            skill_policy=runtime.skill_policy,
            mcp_host=host,
            operation_testing_service=runtime.operation_testing_service,
            api_behavior_monitor_agent=runtime.api_behavior_monitor_agent,
        )
    except BaseException:
        if owns_host:
            try:
                host.close()
            except Exception:
                pass
        raise


def _load_mcp_configs(config: Mapping[str, MCPServerConfig] | str | Path | None) -> dict[str, MCPServerConfig]:
    if config is None:
        return load_mcp_server_configs()
    if isinstance(config, str | Path):
        return load_mcp_server_configs(config)
    return dict(config)
