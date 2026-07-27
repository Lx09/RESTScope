"""Convert MCP tool descriptions into RESTScope ToolSpec objects."""

from __future__ import annotations

from typing import Any

from restscope.llm.schemas import ToolSpec


class MCPToolAdapter:
    """Map MCP tool metadata to the unified tool schema."""

    def to_tool_spec(self, *, server_name: str, mcp_tool: dict) -> ToolSpec:
        """
        Handle to tool spec as part of the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        annotations = self._annotations_dict(self._get(mcp_tool, "annotations"))
        read_only, requires_approval, risk_level = self._classify(annotations)
        return ToolSpec(
            name=f"mcp.{server_name}.{self._get(mcp_tool, 'name')}",
            description=self._get(mcp_tool, "description", "") or "",
            kind="mcp_tool",
            input_schema=self._get(mcp_tool, "inputSchema") or self._get(mcp_tool, "input_schema") or {"type": "object"},
            risk_level=risk_level,
            read_only=read_only,
            requires_approval=requires_approval,
            metadata={
                "server_name": server_name,
                "mcp_tool_name": self._get(mcp_tool, "name"),
                "mcp_annotations": annotations,
            },
        )

    def _classify(self, annotations: dict[str, Any]) -> tuple[bool, bool, str]:
        if not annotations:
            return False, True, "medium"

        read_only = annotations.get("readOnlyHint") is True
        destructive = annotations.get("destructiveHint") is True
        open_world = annotations.get("openWorldHint") is True

        if destructive:
            return False, True, "high"
        if read_only:
            return True, False, "medium" if open_world else "low"
        return False, True, "medium"

    def _annotations_dict(self, annotations: Any) -> dict[str, Any]:
        if annotations is None:
            return {}
        if isinstance(annotations, dict):
            return dict(annotations)
        if hasattr(annotations, "model_dump"):
            return annotations.model_dump(exclude_none=True)
        return {
            key: getattr(annotations, key)
            for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            if hasattr(annotations, key)
        }

    def _get(self, value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)
