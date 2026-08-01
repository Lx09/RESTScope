"""Unified registration for external tool sources."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from restscope.capabilities.agent_tools import AgentToolbox
from restscope.capabilities.mcp import MCPToolAdapter
from restscope.llm.schemas import ToolSpec


CallTool = Callable[[str, dict[str, Any]], Any]


class ToolSourceError(Exception):
    """Base class for tool source registration errors."""


class UnsupportedToolSourceKindError(ToolSourceError):
    """Raised when a source kind has no registered adapter."""


def register_tool_source(
    *,
    toolbox: AgentToolbox,
    server_name: str,
    source: Mapping[str, Any],
    adapter_registry: Mapping[str, Any] | None = None,
) -> list[ToolSpec]:
    """Add every discovered tool from one explicit source to a caller toolbox.

    ``source`` supplies its protocol kind, discovered contracts, and call
    bridge. The returned specifications are the exact values registered. An
    unsupported kind fails during construction before any external call runs.
    """

    kind = source.get("kind")
    if kind != "mcp":
        raise UnsupportedToolSourceKindError(f"Unsupported tool source kind: {kind}")

    adapter = _adapter_for_kind(kind, adapter_registry)
    call_tool = source["call_tool"]
    registered: list[ToolSpec] = []
    for tool in source.get("tools", ()):
        spec = adapter.to_tool_spec(server_name=server_name, mcp_tool=tool)
        source_tool_name = (
            tool.get("name")
            if isinstance(tool, Mapping)
            else getattr(tool, "name")
        )
        toolbox.register(
            spec=spec,
            execute=_build_handler(
                tool_name=str(source_tool_name),
                call_tool=call_tool,
            ),
        )
        registered.append(spec)
    return registered


def _adapter_for_kind(kind: str, adapter_registry: Mapping[str, Any] | None) -> Any:
    """Choose the caller override or RESTScope's built-in MCP adapter."""
    if adapter_registry and kind in adapter_registry:
        return adapter_registry[kind]
    if kind == "mcp":
        return MCPToolAdapter()
    raise UnsupportedToolSourceKindError(f"Unsupported tool source kind: {kind}")


def _build_handler(*, tool_name: str, call_tool: CallTool):
    """Bind the source's original name and bridge before Agent execution."""

    def handler(**arguments: Any) -> dict[str, Any]:
        """Call the external source and normalize its model-facing result."""
        result = call_tool(tool_name, arguments)
        return _normalize_source_result(result)

    return handler


def _normalize_source_result(result: Any) -> dict[str, Any]:
    """Convert MCP-style or plain results into the toolbox output envelope."""
    if isinstance(result, dict):
        structured = result.get("structured")
        if structured is None:
            structured = result.get("structuredContent")
        return {
            "content": _summarize_content(result.get("content")),
            "structured": structured,
            "artifact_ids": result.get("artifact_ids", []),
        }
    return {"content": _summarize_content(result), "structured": None, "artifact_ids": []}


def _summarize_content(content: Any, *, max_chars: int = 2000) -> str:
    """Return a bounded readable summary without changing structured output."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(_content_part_to_text(part) for part in content)
    elif content is None:
        text = ""
    else:
        text = str(content)
    return text[:max_chars]


def _content_part_to_text(part: Any) -> str:
    """Project one MCP content block into its compact text representation."""
    if isinstance(part, dict):
        if part.get("type") == "text" and "text" in part:
            return str(part["text"])
        return str(part)
    return str(part)
