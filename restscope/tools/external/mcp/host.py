"""Lightweight MCP host and stdio client session support."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from queue import Queue
from threading import Thread

from .config import MCPServerConfig


class StdioMCPClientSession:
    """Synchronous wrapper around the official async MCP stdio client."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._commands: Queue[_SessionCommand] = Queue()
        self._thread: Thread | None = None
        self._session: object | None = None

    def start(self) -> None:
        """Start every configured MCP server once and cache its live session."""
        if self._thread is not None:
            return

        ready: Future[None] = Future()
        self._thread = Thread(
            target=self._run_owner,
            args=(ready,),
            name=f"mcp-session-{self.config.name}",
            daemon=True,
        )
        self._thread.start()
        try:
            ready.result(timeout=self.config.timeout)
        except BaseException:
            self._thread.join(timeout=self.config.timeout)
            self._thread = None
            raise

    def list_tools(self) -> list[dict[str, object]]:
        """List the current tool definitions reported by each started MCP server."""
        result = self._request("list_tools")
        return [_to_plain_data(tool) for tool in result.tools]

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        """Call one named tool on an already-started MCP server and return the provider result."""
        result = self._request("call_tool", tool_name, arguments)
        return _to_plain_data(result)

    def close(self) -> None:
        """
        Release resources owned by the policy-controlled model tool boundary.
        """
        thread = self._thread
        if thread is None:
            return

        completed: Future[object] = Future()
        self._commands.put(_SessionCommand(name="close", args=(), completed=completed))
        try:
            completed.result(timeout=self.config.timeout)
        finally:
            thread.join(timeout=self.config.timeout)
            self._thread = None
            self._session = None

    def _request(self, name: str, *args: object) -> object:
        self.start()
        completed: Future[object] = Future()
        self._commands.put(_SessionCommand(name=name, args=args, completed=completed))
        return completed.result()

    def _run_owner(self, ready: Future[None]) -> None:
        try:
            asyncio.run(self._serve(ready))
        except BaseException as exc:  # noqa: BLE001
            if not ready.done():
                ready.set_exception(exc)

    async def _serve(self, ready: Future[None]) -> None:
        """Run the asynchronous MCP host loop, reporting startup or execution failures to the synchronous owner."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env={**os.environ, **self.config.env} if self.config.env else None,
            cwd=self.config.cwd,
        )
        close_command: _SessionCommand | None = None
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self.config.timeout),
                    )
                )
                await session.initialize()
                self._session = session
                ready.set_result(None)

                while True:
                    command = await asyncio.to_thread(self._commands.get)
                    if command.name == "close":
                        close_command = command
                        break
                    try:
                        if command.name == "list_tools":
                            result = await session.list_tools()
                        elif command.name == "call_tool":
                            result = await session.call_tool(*command.args)
                        else:
                            raise RuntimeError(f"Unsupported MCP session command: {command.name}")
                    except BaseException as exc:  # noqa: BLE001
                        command.completed.set_exception(exc)
                    else:
                        command.completed.set_result(result)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            if close_command is not None:
                close_command.completed.set_exception(exc)
            raise
        else:
            if close_command is not None:
                close_command.completed.set_result(None)
        finally:
            self._session = None


@dataclass(frozen=True)
class _SessionCommand:
    name: str
    args: tuple[object, ...]
    completed: Future[object]


MCPSessionFactory = Callable[[MCPServerConfig], object]


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
        self._sessions: dict[str, object] = {}

    def discover_tools(self, server_names: Iterable[str] | None = None) -> dict[str, list[dict[str, object]]]:
        """Return discovered tools grouped by MCP server name."""

        discovered: dict[str, list[dict[str, object]]] = {}
        for server_name in server_names or self.configs:
            if server_name not in self.configs:
                continue
            discovered[server_name] = self._session_for(server_name).list_tools()
        return discovered

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, object]) -> object:
        """Call an MCP tool through the server-specific client session."""

        return self._session_for(server_name).call_tool(tool_name, arguments)

    def close(self) -> None:
        """Close all opened MCP sessions."""

        for session in self._sessions.values():
            close = getattr(session, "close", None)
            if close is not None:
                close()
        self._sessions.clear()

    def _session_for(self, server_name: str) -> object:
        if server_name not in self.configs:
            raise KeyError(f"MCP server is not configured: {server_name}")
        if server_name not in self._sessions:
            session = self._session_factory(self.configs[server_name])
            session.start()
            self._sessions[server_name] = session
        return self._sessions[server_name]


def _to_plain_data(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, list):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain_data(item) for key, item in value.items()}
    return value
