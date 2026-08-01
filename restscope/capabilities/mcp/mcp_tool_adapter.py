"""Convert MCP tool descriptions into RESTScope ToolSpec objects."""

from __future__ import annotations

from typing import Any

from restscope.llm.schemas import ToolSpec


class MCPToolAdapter:
    """Map an MCP description without deciding Agent availability."""

    def to_tool_spec(self, *, server_name: str, mcp_tool: dict) -> ToolSpec:
        """Convert one discovered MCP tool into a provider-neutral contract.

        ``server_name`` namespaces otherwise colliding MCP names. ``mcp_tool``
        may expose camel-case or snake-case schemas; an omitted output schema
        remains absent because MCP sources are allowed not to provide one.
        """
        return ToolSpec(
            name=f"mcp.{server_name}.{self._get(mcp_tool, 'name')}",
            description=self._get(mcp_tool, "description", "") or "",
            kind="mcp_tool",
            input_schema=(
                self._get(mcp_tool, "inputSchema")
                or self._get(mcp_tool, "input_schema")
                or {"type": "object"}
            ),
            output_schema=(
                self._get(mcp_tool, "outputSchema")
                or self._get(mcp_tool, "output_schema")
            ),
        )

    def _get(self, value: Any, key: str, default: Any = None) -> Any:
        """Read one field from either an MCP dictionary or SDK object."""
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)
