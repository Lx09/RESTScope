# MCP Adapter Integration

## Status

Completed.

## Goal

Let RESTScope consume externally discovered MCP tools through the generic
capability layer without owning MCP server startup, transport, or session
lifecycle.

## Scope

- Use MCP annotations as the primary source for read-only and risk mapping.
- Register MCP tools through a Tool-layer helper that accepts external tool
  definitions and an external `call_tool` bridge.
- Provide unified `add_preset_tools` and `build_capabilities` entrypoints for
  RESTScope-supported preset tool sources, starting with `schemathesis`.
- Allow read-only MCP tools for tool-capable roles through generic policy.
- Document MCP server configuration as an external MCP Host responsibility.

## Out Of Scope

- RESTScope-managed MCP server configuration.
- RESTScope-managed stdio transport or MCP sessions.
- MCP-specific public registration APIs.
- Schemathesis-specific runtime code.

## Verification

- `uv run pytest -q`
- `uv run python -c "from restscope.capabilities import build_capabilities, add_preset_tools"`
