"""One-call construction for RESTScope capability runtime objects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from restscope.capabilities.skills import SkillManifest, SkillPolicy, SkillRegistry
from restscope.capabilities.tool_call_validator import ToolCallValidator
from restscope.capabilities.tool_executor import ToolExecutor
from restscope.capabilities.tool_policy import ToolPolicy
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.capabilities.tool_selector import ToolSelector
from restscope.capabilities.tool_sources import add_preset_tools


@dataclass(frozen=True)
class CapabilityRuntime:
    """Runtime bundle for tools and prompt-only skill metadata."""

    tool_registry: ToolRegistry
    tool_policy: ToolPolicy
    tool_selector: ToolSelector
    tool_executor: ToolExecutor
    skill_registry: SkillRegistry
    skill_policy: SkillPolicy


def build_capabilities(
    *,
    sources: Mapping[str, Mapping[str, Any]] | None = None,
    presets: Iterable[str] = ("schemathesis",),
    skills: Iterable[SkillManifest] = (),
) -> CapabilityRuntime:
    """Build a complete capability runtime from external sources and skills."""

    tool_registry = ToolRegistry()
    tool_policy = ToolPolicy()
    tool_selector = ToolSelector(tool_registry, tool_policy)
    tool_validator = ToolCallValidator(tool_registry, tool_policy)
    tool_executor = ToolExecutor(tool_registry, tool_validator)
    skill_registry = SkillRegistry()
    skill_policy = SkillPolicy()

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
