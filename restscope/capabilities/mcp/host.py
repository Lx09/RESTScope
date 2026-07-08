"""Lightweight MCP host and stdio client session support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from contextlib import AsyncExitStack
from datetime import timedelta
import os
from typing import Any

from .config import MCPServerConfig


class StdioMCPClientSession:
    """Synchronous wrapper around the official async MCP stdio client."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stack: AsyncExitStack | None = None
        self._session: Any | None = None

    def start(self) -> None:
        if self._session is not None:
            return

        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._start_async())

    def list_tools(self) -> list[dict[str, Any]]:
        self.start()
        result = self._run(self._session.list_tools())
        return [_to_plain_data(tool) for tool in result.tools]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.start()
        result = self._run(self._session.call_tool(tool_name, arguments))
        return _to_plain_data(result)

    def close(self) -> None:
        if self._loop is None:
            return
        if self._stack is not None:
            self._loop.run_until_complete(self._stack.aclose())
        self._loop.close()
        self._loop = None
        self._stack = None
        self._session = None

    async def _start_async(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env={**os.environ, **self.config.env} if self.config.env else None,
            cwd=self.config.cwd,
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=self.config.timeout),
            )
        )
        await session.initialize()
        self._session = session

    def _run(self, coroutine):
        if self._loop is None:
            raise RuntimeError("MCP session is not started.")
        return self._loop.run_until_complete(coroutine)


MCPSessionFactory = Callable[[MCPServerConfig], Any]


class MCPHost:
    """Manage RESTScope-owned MCP server sessions."""

    def __init__(
        self,
        configs: Mapping[str, MCPServerConfig],
        *,
        session_factory: MCPSessionFactory | None = None,
    ) -> None:
        self.configs = dict(configs)
        self._session_factory = session_factory or StdioMCPClientSession
        self._sessions: dict[str, Any] = {}

    def discover_tools(self, server_names: Iterable[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Return discovered tools grouped by MCP server name."""

        discovered: dict[str, list[dict[str, Any]]] = {}
        for server_name in server_names or self.configs:
            if server_name not in self.configs:
                continue
            discovered[server_name] = self._session_for(server_name).list_tools()
        return discovered

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool through the server-specific client session."""

        return self._session_for(server_name).call_tool(tool_name, arguments)

    def close(self) -> None:
        """Close all opened MCP sessions."""

        for session in self._sessions.values():
            close = getattr(session, "close", None)
            if close is not None:
                close()
        self._sessions.clear()

    def _session_for(self, server_name: str) -> Any:
        if server_name not in self.configs:
            raise KeyError(f"MCP server is not configured: {server_name}")
        if server_name not in self._sessions:
            session = self._session_factory(self.configs[server_name])
            session.start()
            self._sessions[server_name] = session
        return self._sessions[server_name]


def _to_plain_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    return value
