"""Convert MCP tool descriptions into RESTScope ToolSpec objects."""

from __future__ import annotations

from restscope.llm.schemas import ToolSpec


class MCPToolAdapter:
    """Map MCP tool metadata to the unified tool schema."""

    WRITE_KEYWORDS = {"write", "delete", "update", "insert", "execute", "run", "send", "create"}

    def to_tool_spec(self, *, server_name: str, mcp_tool: dict) -> ToolSpec:
        read_only = self._infer_read_only(mcp_tool)
        return ToolSpec(
            name=f"mcp.{server_name}.{mcp_tool['name']}",
            description=mcp_tool.get("description", ""),
            kind="mcp_tool",
            input_schema=mcp_tool["inputSchema"],
            risk_level="low" if read_only else "medium",
            read_only=read_only,
            requires_approval=not read_only,
            metadata={
                "server_name": server_name,
                "mcp_tool_name": mcp_tool["name"],
            },
        )

    def _infer_read_only(self, mcp_tool: dict) -> bool:
        haystack = f"{mcp_tool.get('name', '')} {mcp_tool.get('description', '')}".lower()
        return not any(keyword in haystack for keyword in self.WRITE_KEYWORDS)
