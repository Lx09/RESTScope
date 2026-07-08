"""Unified registration for external tool sources."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from restscope.capabilities.mcp import MCPToolAdapter
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolSpec


CallTool = Callable[[str, dict[str, Any]], Any]
SUPPORTED_PRESET_TOOL_SOURCES = {"schemathesis": "mcp"}
DEFAULT_PRESET_TOOL_SOURCES = ("schemathesis",)


class ToolSourceError(Exception):
    """Base class for tool source registration errors."""


class PresetToolSourceNotFoundError(ToolSourceError):
    """Raised when a requested preset source was not provided."""


class UnsupportedPresetToolSourceError(ToolSourceError):
    """Raised when a requested preset source is unknown to RESTScope."""


class UnsupportedToolSourceKindError(ToolSourceError):
    """Raised when a source kind has no registered adapter."""


def add_preset_tools(
    *,
    registry: ToolRegistry,
    sources: Mapping[str, Mapping[str, Any]],
    presets: Iterable[str] = DEFAULT_PRESET_TOOL_SOURCES,
    adapter_registry: Mapping[str, Any] | None = None,
) -> list[ToolSpec]:
    """Register RESTScope-supported preset tools from external sources."""

    registered: list[ToolSpec] = []
    for preset in presets:
        if preset not in SUPPORTED_PRESET_TOOL_SOURCES:
            raise UnsupportedPresetToolSourceError(f"Unsupported preset tool source: {preset}")
        source = sources.get(preset)
        if source is None:
            raise PresetToolSourceNotFoundError(f"Preset tool source not available: {preset}")
        registered.extend(
            register_tool_source(
                registry=registry,
                server_name=preset,
                source=source,
                adapter_registry=adapter_registry,
            )
        )
    return registered


def register_tool_source(
    *,
    registry: ToolRegistry,
    server_name: str,
    source: Mapping[str, Any],
    adapter_registry: Mapping[str, Any] | None = None,
) -> list[ToolSpec]:
    """Register all tools from one external source."""

    kind = source.get("kind")
    if kind != "mcp":
        raise UnsupportedToolSourceKindError(f"Unsupported tool source kind: {kind}")

    adapter = _adapter_for_kind(kind, adapter_registry)
    call_tool = source["call_tool"]
    registered: list[ToolSpec] = []
    for tool in source.get("tools", ()):
        spec = adapter.to_tool_spec(server_name=server_name, mcp_tool=tool)
        registry.register(
            spec=spec,
            handler=_build_handler(
                tool_name=spec.metadata["mcp_tool_name"],
                call_tool=call_tool,
            ),
        )
        registered.append(spec)
    return registered


def _adapter_for_kind(kind: str, adapter_registry: Mapping[str, Any] | None) -> Any:
    if adapter_registry and kind in adapter_registry:
        return adapter_registry[kind]
    if kind == "mcp":
        return MCPToolAdapter()
    raise UnsupportedToolSourceKindError(f"Unsupported tool source kind: {kind}")


def _build_handler(*, tool_name: str, call_tool: CallTool):
    def handler(**arguments: Any) -> dict[str, Any]:
        result = call_tool(tool_name, arguments)
        return _normalize_source_result(result)

    return handler


def _normalize_source_result(result: Any) -> dict[str, Any]:
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
    if isinstance(part, dict):
        if part.get("type") == "text" and "text" in part:
            return str(part["text"])
        return str(part)
    return str(part)
