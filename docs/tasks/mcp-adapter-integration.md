# MCP Adapter and Lightweight Host Integration

## Status

Completed.

## Goal

Let RESTScope run independently with a lightweight stdio MCP Host while keeping
tool registration, selection, and execution policy in the generic capability
layer.

## Scope

- Use MCP annotations as the primary source for read-only and risk mapping.
- Register MCP tools through a Tool-layer helper that accepts external tool
  definitions and an external `call_tool` bridge.
- Provide unified `add_preset_tools` and `build_capabilities` entrypoints for
  RESTScope-supported preset tool sources, starting with `schemathesis`.
- Provide `MCPHost`, `MCPServerConfig`, and `MCPSourceBuilder` for standalone
  stdio MCP discovery and call bridging.
- Provide `build_capabilities_with_mcp_host` as the standalone shortcut.
- Allow read-only MCP tools for tool-capable roles through generic policy.
- Keep `.env` short by pointing to `MCP_SERVERS_FILE`; command/env details live
  in `mcp.servers.json`.

## Out Of Scope

- MCP-specific public registration APIs.
- Schemathesis-specific runtime code.
- SSE/HTTP MCP transports.
- Background daemon management, health checks, and reconnect policy.

## Verification

- `uv run pytest -q`
- `uv run python -c "from restscope.capabilities.mcp import MCPHost"`
- `uv run python -c "from restscope.capabilities import build_capabilities_with_mcp_host"`
