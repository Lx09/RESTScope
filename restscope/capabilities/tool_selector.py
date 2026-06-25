"""Select model-visible tools for a role."""

from __future__ import annotations

from restscope.capabilities.tool_policy import ToolPolicy
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolSpec


class ToolSelector:
    """Filter registered tools through the role policy."""

    def __init__(self, registry: ToolRegistry, policy: ToolPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or ToolPolicy()

    def select_for_role(self, *, role: str, state: dict) -> list[ToolSpec]:
        return [
            tool
            for tool in self.registry.list_specs()
            if self.policy.is_allowed(role=role, tool_spec=tool, state=state)
        ]
