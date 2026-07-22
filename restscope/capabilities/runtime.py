"""One-call construction for RESTScope capability runtime objects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from restscope.capabilities.mcp import MCPHost, MCPServerConfig, MCPSourceBuilder, load_mcp_server_configs
from restscope.capabilities.http_request import register_http_request_tool
from restscope.capabilities.skills import SkillManifest, SkillPolicy, SkillRegistry
from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_executor import ToolExecutor
from restscope.capabilities.tool_policy import ToolPolicy
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.capabilities.tool_selector import ToolSelector
from restscope.capabilities.tool_sources import add_preset_tools
from restscope.observability import TracingRuntime


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


def build_capabilities(
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    presets: Iterable[str] = ("schemathesis",),
    skills: Iterable[SkillManifest] = (),
    tracing_runtime: TracingRuntime | None = None,
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

    register_http_request_tool(tool_registry)

    for skill in skills:
        skill_registry.register(skill)

    preset_list = tuple(presets)
    if preset_list:
        add_preset_tools(registry=tool_registry, sources=sources or {}, presets=preset_list)

    return CapabilityRuntime(
        tool_registry=tool_registry,
        tool_policy=tool_policy,
        tool_selector=tool_selector,
        tool_executor=tool_executor,
        skill_registry=skill_registry,
        skill_policy=skill_policy,
    )


def build_capabilities_with_mcp_host(
    *,
    config: Mapping[str, MCPServerConfig] | str | Path | None = None,
    mcp_host: MCPHost | None = None,
    presets: Iterable[str] = ("schemathesis",),
    skills: Iterable[SkillManifest] = (),
    tracing_runtime: TracingRuntime | None = None,
) -> CapabilityRuntime:
    """Build capabilities after discovering tools through RESTScope's MCP host."""

    host = mcp_host or MCPHost(_load_mcp_configs(config))
    preset_list = tuple(presets)
    sources = MCPSourceBuilder(host).build_sources(presets=preset_list)
    runtime = build_capabilities(
        sources=sources,
        presets=preset_list,
        skills=skills,
        tracing_runtime=tracing_runtime,
    )
    return CapabilityRuntime(
        tool_registry=runtime.tool_registry,
        tool_policy=runtime.tool_policy,
        tool_selector=runtime.tool_selector,
        tool_executor=runtime.tool_executor,
        skill_registry=runtime.skill_registry,
        skill_policy=runtime.skill_policy,
        mcp_host=host,
    )


def _load_mcp_configs(config: Mapping[str, MCPServerConfig] | str | Path | None) -> dict[str, MCPServerConfig]:
    if config is None:
        return load_mcp_server_configs()
    if isinstance(config, str | Path):
        return load_mcp_server_configs(config)
    return dict(config)
