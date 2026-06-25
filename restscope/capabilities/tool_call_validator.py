"""Validate model-requested tool calls before execution."""

from __future__ import annotations

from typing import Any

from restscope.capabilities.tool_policy import ToolPolicy
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolCall


class ToolCallValidator:
    """Return structured validation errors instead of raising for denials."""

    def __init__(self, registry: ToolRegistry, policy: ToolPolicy) -> None:
        self.registry = registry
        self.policy = policy

    def validate(self, *, tool_call: ToolCall, role: str, state: dict) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        try:
            spec = self.registry.get_spec(tool_call.name)
        except KeyError:
            return [{"type": "unknown_tool", "message": f"Unknown tool: {tool_call.name}"}]

        if not self.policy.is_allowed(role=role, tool_spec=spec, state=state):
            errors.append(
                {
                    "type": "tool_not_allowed",
                    "message": f"Tool not allowed for role {role}: {tool_call.name}",
                }
            )

        if spec.requires_approval:
            errors.append(
                {
                    "type": "approval_required",
                    "message": f"Tool requires approval: {tool_call.name}",
                }
            )

        return errors
