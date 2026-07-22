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
    def __init__(self, config, *, tools: list[dict], calls: list[tuple[str, dict]]):
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
            "content": [{"type": "text", "text": "run details"}],
            "structuredContent": {"tool_name": tool_name, "arguments": arguments},
        }

    def close(self) -> None:
        self.closed = True


def test_load_mcp_server_configs_reads_file_and_env_var(tmp_path, monkeypatch) -> None:
    from restscope.capabilities.mcp import load_mcp_server_configs

    config_path = tmp_path / "mcp.servers.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "schemathesis": {
                        "command": "/bin/schemathesis-mcp",
                        "args": ["--stdio"],
                        "env": {"SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost"},
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
    assert explicit["schemathesis"].name == "schemathesis"
    assert explicit["schemathesis"].command == "/bin/schemathesis-mcp"
    assert explicit["schemathesis"].args == ["--stdio"]
    assert explicit["schemathesis"].env == {"SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost"}
    assert explicit["schemathesis"].cwd == tmp_path
    assert explicit["schemathesis"].timeout == 45


def test_mcp_host_discovers_tools_and_calls_original_tool_name() -> None:
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig

    calls: list[tuple[str, dict]] = []
    sessions: list[FakeMCPSession] = []

    def session_factory(config):
        session = FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "get_run",
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
        {"schemathesis": MCPServerConfig(name="schemathesis", command="/bin/schemathesis-mcp")},
        session_factory=session_factory,
    )

    tools = host.discover_tools()
    result = host.call_tool("schemathesis", "get_run", {"run_id": "run_1"})
    host.close()

    assert tools == {"schemathesis": [_mcp_tool("get_run", sessions[0].tools[0]["annotations"])]}
    assert calls == [("get_run", {"run_id": "run_1"})]
    assert result["structuredContent"] == {"tool_name": "get_run", "arguments": {"run_id": "run_1"}}
    assert sessions[0].closed is True


def test_mcp_source_builder_registers_discovered_tools_through_existing_runtime(tool_context) -> None:
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry, add_preset_tools
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig, MCPSourceBuilder
    from restscope.llm import ToolCall

    calls: list[tuple[str, dict]] = []

    def session_factory(config):
        return FakeMCPSession(
            config,
            tools=[
                _mcp_tool(
                    "get_run",
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
        {"schemathesis": MCPServerConfig(name="schemathesis", command="/bin/schemathesis-mcp")},
        session_factory=session_factory,
    )
    sources = MCPSourceBuilder(host).build_sources(presets=("schemathesis",))
    registry = ToolRegistry()
    registered = add_preset_tools(registry=registry, sources=sources)
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    executor.bind_context(tool_context)

    result = executor.execute(
        tool_call=ToolCall(id="call_1", name="mcp.schemathesis.get_run", arguments={"run_id": "run_1"}),
        role="planner",
        state={},
    )

    assert [tool.name for tool in registered] == ["mcp.schemathesis.get_run"]
    assert calls == [("get_run", {"run_id": "run_1"})]
    assert result.status == "succeeded"
    assert result.structured == {"tool_name": "get_run", "arguments": {"run_id": "run_1"}}


def test_build_capabilities_with_mcp_host_registers_read_only_tools_and_denies_destructive() -> None:
    from restscope.capabilities import build_capabilities_with_mcp_host
    from restscope.capabilities.mcp import MCPHost, MCPServerConfig

    def session_factory(config):
        return FakeMCPSession(
            config,
            tools=[
                _mcp_tool("get_run", {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}),
                _mcp_tool("start_run", {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}),
            ],
            calls=[],
        )

    host = MCPHost(
        {"schemathesis": MCPServerConfig(name="schemathesis", command="/bin/schemathesis-mcp")},
        session_factory=session_factory,
    )

    runtime = build_capabilities_with_mcp_host(mcp_host=host)

    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request",
        "mcp.schemathesis.get_run",
        "mcp.schemathesis.start_run",
    ]
    assert [tool.name for tool in runtime.tool_selector.select_for_role(role="planner", state={})] == [
        "restscope.http.request",
        "mcp.schemathesis.get_run"
    ]


def test_build_capabilities_with_mcp_host_raises_when_preset_missing() -> None:
    from restscope.capabilities import PresetToolSourceNotFoundError, build_capabilities_with_mcp_host
    from restscope.capabilities.mcp import MCPHost

    host = MCPHost({}, session_factory=lambda config: None)

    with pytest.raises(PresetToolSourceNotFoundError, match="Preset tool source not available: schemathesis"):
        build_capabilities_with_mcp_host(mcp_host=host)
