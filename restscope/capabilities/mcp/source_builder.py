"""Build unified capability sources from an MCP host."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .host import MCPHost


class MCPSourceBuilder:
    """Convert discovered MCP tools into RESTScope tool source mappings."""

    def __init__(self, host: MCPHost) -> None:
        self.host = host

    def build_sources(
        self,
        *,
        server_names: Iterable[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        discovered = self.host.discover_tools(server_names=server_names)
        return {
            server_name: {
                "kind": "mcp",
                "tools": tools,
                "call_tool": self._call_bridge(server_name),
            }
            for server_name, tools in discovered.items()
        }

    def _call_bridge(self, server_name: str):
        def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
            return self.host.call_tool(server_name, tool_name, arguments)

        return call_tool
