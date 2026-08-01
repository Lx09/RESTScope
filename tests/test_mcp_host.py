"""Regression scenarios for mcp host. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json

import pytest


def _mcp_tool(name: str, annotations: dict | None = None) -> dict:
    tool = {
        "name": name,
        "description": f"{name} description",
        "inputSchema": {"type": "object"},
    }
    if annotations is not None:
        tool["annotations"] = annotations
    return tool


class FakeMCPSession:
    def __init__(
        self,
        config,
        *,
        tools: list[dict],
        calls: list[tuple[str, dict]],
    ):
        self.config = config
        self.tools = tools
        self.calls = calls
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def list_tools(self) -> list[dict]:
        assert self.started is True
        return self.tools

    def call_tool(self, tool_name: str, arguments: dict):
        assert self.started is True
        self.calls.append((tool_name, arguments))
        return {
            "content": [{"type": "text", "text": "details"}],
            "structuredContent": {
                "tool_name": tool_name,
                "arguments": arguments,
            },
        }

    def close(self) -> None:
        self.closed = True


def test_load_mcp_server_configs_reads_file_and_env_var(
    tmp_path,
    monkeypatch,
) -> None:
    """Scenario: verify that load mcp server configs reads file and env var."""
    from restscope.capabilities.mcp import load_mcp_server_configs

    config_path = tmp_path / "mcp.servers.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "example": {
                        "command": "/bin/example-mcp",
                        "args": ["--stdio"],
                        "env": {"EXAMPLE_MODE": "local"},
                        "cwd": str(tmp_path),
                        "timeout": 45,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    explicit = load_mcp_server_configs(config_path)
    monkeypatch.setenv("MCP_SERVERS_FILE", str(config_path))
    from_env = load_mcp_server_configs()

    assert explicit == from_env
    assert explicit["example"].name == "example"
    assert explicit["example"].command == "/bin/example-mcp"
    assert explicit["example"].args == ["--stdio"]
    assert explicit["example"].env == {"EXAMPLE_MODE": "local"}
    assert explicit["example"].cwd == tmp_path
    assert explicit["example"].timeout == 45


def test_mcp_host_discovers_tools_calls_original_name_and_closes() -> None:
    """Scenario: verify that mcp host discovers tools calls original name and closes."""
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig

    calls: list[tuple[str, dict]] = []
    sessions: list[FakeMCPSession] = []

    def session_factory(config):
        session = FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "inspect",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                )
            ],
            calls=calls,
        )
        sessions.append(session)
        return session

    host = MCPHost(
        {
            "example": MCPServerConfig(
                name="example",
                command="/bin/example-mcp",
            )
        },
        session_factory=session_factory,
    )

    tools = host.discover_tools()
    result = host.call_tool("example", "inspect", {"item_id": "item_1"})
    host.close()

    assert tools == {
        "example": [_mcp_tool("inspect", sessions[0].tools[0]["annotations"])]
    }
    assert calls == [("inspect", {"item_id": "item_1"})]
    assert result["structuredContent"] == {
        "tool_name": "inspect",
        "arguments": {"item_id": "item_1"},
    }
    assert sessions[0].closed is True


def test_mcp_source_builder_registers_discovered_tools_through_runtime(
) -> None:
    """Scenario: verify that mcp source builder registers discovered tools through runtime."""
    from restscope.capabilities import (
        AgentToolbox,
        register_tool_source,
    )
    from restscope.capabilities.mcp import (
        MCPHost,
        MCPServerConfig,
        MCPSourceBuilder,
    )
    from restscope.llm import ToolCall

    calls: list[tuple[str, dict]] = []

    def session_factory(config):
        return FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "inspect",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                )
            ],
            calls=calls,
        )

    host = MCPHost(
        {
            "example": MCPServerConfig(
                name="example",
                command="/bin/example-mcp",
            )
        },
        session_factory=session_factory,
    )
    sources = MCPSourceBuilder(host).build_sources(server_names=("example",))
    toolbox = AgentToolbox()
    registered = []
    for server_name, source in sources.items():
        registered.extend(
            register_tool_source(
                toolbox=toolbox,
                server_name=server_name,
                source=source,
            )
        )
    result = toolbox.execute(
        ToolCall(
            id="call_1",
            name="mcp.example.inspect",
            arguments={"item_id": "item_1"},
        )
    )

    assert [tool.name for tool in registered] == ["mcp.example.inspect"]
    assert calls == [("inspect", {"item_id": "item_1"})]
    assert result.status == "succeeded"
    assert result.structured == {
        "tool_name": "inspect",
        "arguments": {"item_id": "item_1"},
    }


def test_build_capabilities_with_mcp_host_discovers_all_servers_by_default() -> None:
    """Scenario: verify that build capabilities with mcp host discovers all servers by default."""
    from restscope.capabilities import build_capabilities_with_mcp_host
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig

    def session_factory(config):
        return FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "inspect",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "openWorldHint": False,
                    },
                ),
                _mcp_tool(
                    "mutate",
                    {
                        "readOnlyHint": False,
                        "destructiveHint": True,
                        "openWorldHint": True,
                    },
                ),
            ],
            calls=[],
        )

    host = MCPHost(
        {
            "example": MCPServerConfig(
                name="example",
                command="/bin/example-mcp",
            ),
            "secondary": MCPServerConfig(
                name="secondary",
                command="/bin/secondary-mcp",
            ),
        },
        session_factory=session_factory,
    )

    runtime = build_capabilities_with_mcp_host(mcp_host=host)

    assert runtime.external_tools is not None
    assert [tool.name for tool in runtime.external_tools.specs()] == [
        "mcp.example.inspect",
        "mcp.example.mutate",
        "mcp.secondary.inspect",
        "mcp.secondary.mutate",
    ]


def test_build_capabilities_with_mcp_host_can_filter_generic_server_names() -> None:
    """Scenario: verify that build capabilities with mcp host can filter generic server names."""
    from restscope.capabilities import build_capabilities_with_mcp_host
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig

    def session_factory(config):
        return FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "inspect",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "openWorldHint": False,
                    },
                )
            ],
            calls=[],
        )

    host = MCPHost(
        {
            "example": MCPServerConfig(
                name="example",
                command="/bin/example-mcp",
            ),
            "secondary": MCPServerConfig(
                name="secondary",
                command="/bin/secondary-mcp",
            ),
        },
        session_factory=session_factory,
    )

    runtime = build_capabilities_with_mcp_host(
        mcp_host=host,
        server_names=("secondary",),
    )

    assert runtime.external_tools is not None
    assert [tool.name for tool in runtime.external_tools.specs()] == [
        "mcp.secondary.inspect",
    ]


def test_build_capabilities_with_empty_mcp_host_has_no_external_tools() -> None:
    """An empty MCP host does not create an executable toolbox."""
    from restscope.capabilities import build_capabilities_with_mcp_host
    from restscope.capabilities.mcp import MCPHost

    runtime = build_capabilities_with_mcp_host(
        mcp_host=MCPHost({}, session_factory=lambda config: None)
    )

    assert runtime.external_tools is None
    assert runtime.target_http_tool is not None


def test_build_capabilities_with_mcp_host_closes_owned_host_on_discovery_failure(
    monkeypatch,
) -> None:
    """Scenario: verify that build capabilities with mcp host closes owned host on discovery failure."""
    from restscope.capabilities import build_capabilities_with_mcp_host

    class Host:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    host = Host()
    monkeypatch.setattr("restscope.capabilities.runtime.MCPHost", lambda _configs: host)
    monkeypatch.setattr(
        "restscope.capabilities.runtime.MCPSourceBuilder.build_sources",
        lambda self, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("discovery failed")
        ),
    )

    with pytest.raises(RuntimeError, match="discovery failed"):
        build_capabilities_with_mcp_host(config={})

    assert host.closed is True


def test_build_capabilities_with_mcp_host_keeps_injected_host_on_failure(
    monkeypatch,
) -> None:
    """Scenario: verify that build capabilities with mcp host keeps injected host on failure."""
    from restscope.capabilities import build_capabilities_with_mcp_host

    class Host:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    host = Host()
    monkeypatch.setattr(
        "restscope.capabilities.runtime.MCPSourceBuilder.build_sources",
        lambda self, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("discovery failed")
        ),
    )

    with pytest.raises(RuntimeError, match="discovery failed"):
        build_capabilities_with_mcp_host(mcp_host=host)

    assert host.closed is False


def test_build_capabilities_with_mcp_host_closes_owned_host_on_interrupt(
    monkeypatch,
) -> None:
    """Scenario: verify that build capabilities with mcp host closes owned host on interrupt."""
    from restscope.capabilities import build_capabilities_with_mcp_host

    class Host:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    host = Host()
    monkeypatch.setattr("restscope.capabilities.runtime.MCPHost", lambda _configs: host)
    monkeypatch.setattr(
        "restscope.capabilities.runtime.MCPSourceBuilder.build_sources",
        lambda self, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        build_capabilities_with_mcp_host(config={})

    assert host.closed is True
