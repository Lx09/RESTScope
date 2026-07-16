from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "get_capabilities",
    "start_run",
    "get_run",
    "get_events",
    "get_result",
    "get_failure",
    "cancel_run",
}


def test_in_repo_schemathesis_server_matches_operation_runner_contract(tmp_path: Path) -> None:
    from restscope.capabilities.mcp import MCPHost, load_mcp_server_configs

    configs = load_mcp_server_configs(REPO_ROOT / "mcp.servers.example.json")
    config = configs["schemathesis"]

    assert config.command == "uv"
    assert config.args == [
        "run",
        "--project",
        "services/schemathesis-mcp",
        "schemathesis-mcp",
    ]
    assert config.cwd == Path(".")

    config.env["SCHEMATHESIS_MCP_ARTIFACT_DIR"] = str(tmp_path / "artifacts")
    host = MCPHost(configs)
    try:
        tools = {tool["name"]: tool for tool in host.discover_tools()["schemathesis"]}
    finally:
        host.close()

    assert set(tools) == EXPECTED_TOOLS

    start_run = tools["start_run"]
    assert start_run["annotations"] == {
        "title": "Start run",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }
    assert start_run["inputSchema"]["required"] == ["schema"]
    assert {
        "schema",
        "base_url",
        "headers",
        "phases",
        "checks",
        "generation_modes",
        "include",
        "max_examples",
        "max_failures",
        "max_time",
        "seed",
    }.issubset(start_run["inputSchema"]["properties"])

    for tool_name in ("get_capabilities", "get_run", "get_result"):
        assert tools[tool_name]["annotations"]["readOnlyHint"] is True
        assert tools[tool_name]["annotations"]["destructiveHint"] is False

    for tool_name in ("get_run", "get_result"):
        assert tools[tool_name]["inputSchema"]["required"] == ["run_id"]
