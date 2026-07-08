"""Configuration objects for RESTScope-managed MCP servers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_MCP_SERVERS_FILE = Path("./mcp.servers.json")


@dataclass(frozen=True)
class MCPServerConfig:
    """Connection settings for one stdio MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    timeout: int = 30

    @classmethod
    def from_mapping(cls, name: str, values: Mapping[str, Any]) -> "MCPServerConfig":
        return cls(
            name=name,
            command=str(values["command"]),
            args=[str(arg) for arg in values.get("args", [])],
            env={str(key): str(value) for key, value in values.get("env", {}).items()},
            cwd=Path(values["cwd"]).expanduser() if values.get("cwd") else None,
            timeout=int(values.get("timeout", 30)),
        )


def load_mcp_server_configs(
    config_file: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, MCPServerConfig]:
    """Load MCP server definitions from `MCP_SERVERS_FILE` or the default file."""

    values = env or os.environ
    path = Path(config_file or values.get("MCP_SERVERS_FILE", DEFAULT_MCP_SERVERS_FILE)).expanduser()
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    servers = raw.get("mcpServers", raw)
    return {
        name: MCPServerConfig.from_mapping(name, server_config)
        for name, server_config in servers.items()
    }
