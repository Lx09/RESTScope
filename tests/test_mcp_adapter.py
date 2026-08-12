"""Regression scenarios for mcp adapter. Each test documents one observable contract or failure boundary."""

from __future__ import annotations


def _mcp_tool(
    name: str,
    annotations: dict | None,
    input_schema: dict | None = None,
) -> dict:
    tool = {
        "name": name,
        "description": f"{name} description",
        "inputSchema": input_schema or {"type": "object"},
    }
    if annotations is not None:
        tool["annotations"] = annotations
    return tool


def test_mcp_tool_adapter_preserves_contract_without_deciding_availability() -> None:
    """MCP annotations do not become a hidden central permission policy."""
    from restscope.tools.external.mcp import MCPToolAdapter

    adapter = MCPToolAdapter()
    read_only = adapter.to_tool_spec(
        server_name="example",
        mcp_tool=_mcp_tool(
            "inspect",
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        ),
    )
    destructive = adapter.to_tool_spec(
        server_name="example",
        mcp_tool=_mcp_tool(
            "mutate",
            {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        ),
    )
    open_world = adapter.to_tool_spec(
        server_name="external",
        mcp_tool=_mcp_tool(
            "fetch_remote",
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        ),
    )
    missing = adapter.to_tool_spec(
        server_name="unknown",
        mcp_tool=_mcp_tool("get_status", None),
    )

    assert read_only.name == "mcp.example.inspect"
    assert not hasattr(read_only, "metadata")
    assert destructive.name == "mcp.example.mutate"
    assert open_world.name == "mcp.external.fetch_remote"
    assert missing.name == "mcp.unknown.get_status"
    for spec in (read_only, destructive, open_world, missing):
        assert "read_only" not in type(spec).model_fields
        assert "requires_approval" not in type(spec).model_fields
        assert "risk_level" not in type(spec).model_fields


def test_register_tool_source_uses_external_call_bridge_and_summarizes_results(
) -> None:
    """Scenario: verify that register tool source uses external call bridge and summarizes results."""
    from restscope.tools import AgentToolbox
    from restscope.tools.external import register_tool_source
    from restscope.llm import ToolCall

    calls: list[tuple[str, dict]] = []

    def call_tool(tool_name: str, arguments: dict):
        calls.append((tool_name, arguments))
        return {
            "content": "x" * 2500,
            "structured": {"tool_name": tool_name, "arguments": arguments},
            "artifact_ids": ["artifact_1"],
        }

    toolbox = AgentToolbox()
    registered = register_tool_source(
        toolbox=toolbox,
        server_name="example",
        source={
            "kind": "mcp",
            "tools": [
                _mcp_tool(
                    "inspect",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                    input_schema={
                        "type": "object",
                        "properties": {"item_id": {"type": "string"}},
                        "required": ["item_id"],
                    },
                )
            ],
            "call_tool": call_tool,
        },
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
    assert result.content is not None
    assert len(result.content) <= 2000
    assert result.structured == {
        "tool_name": "inspect",
        "arguments": {"item_id": "item_1"},
    }
    assert result.artifact_ids == ["artifact_1"]


def test_register_tool_source_rejects_unsupported_source_kind() -> None:
    """Scenario: verify that register tool source rejects unsupported source kind."""
    import pytest

    from restscope.tools import AgentToolbox
    from restscope.tools.external import (
        UnsupportedToolSourceKindError,
        register_tool_source,
    )

    with pytest.raises(
        UnsupportedToolSourceKindError,
        match="Unsupported tool source kind: skill",
    ):
        register_tool_source(
            toolbox=AgentToolbox(),
            server_name="some_skill",
            source={
                "kind": "skill",
                "tools": [],
                "call_tool": lambda tool_name, arguments: {},
            },
        )


def test_build_harness_registers_all_explicit_sources_without_presets() -> None:
    """Scenario: verify that build capabilities registers all explicit sources without presets."""
    import inspect

    import restscope.harness as harness

    runtime = harness.build_harness(
        sources={
            "example": {
                "kind": "mcp",
                "tools": [
                    _mcp_tool(
                        "inspect",
                        {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "openWorldHint": False,
                        },
                    )
                ],
                "call_tool": lambda tool_name, arguments: {
                    "content": tool_name,
                    "structured": arguments,
                },
            },
            "secondary": {
                "kind": "mcp",
                "tools": [],
                "call_tool": lambda tool_name, arguments: {},
            },
        }
    )

    assert runtime.external_tools is not None
    assert [tool.name for tool in runtime.external_tools.specs()] == [
        "mcp.example.inspect"
    ]
    assert "presets" not in inspect.signature(
        harness.build_harness
    ).parameters
    assert not hasattr(harness, "add_preset_tools")


def test_build_harness_initializes_external_tools_without_implicit_skills() -> None:
    """External discovery populates its Catalog but grants no prompt instructions."""
    from restscope.harness import build_harness

    runtime = build_harness(
        sources={
            "example": {
                "kind": "mcp",
                "tools": [
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
                "call_tool": lambda tool_name, arguments: {"content": "ok"},
            }
        },
    )

    assert runtime.external_tools is not None
    assert [tool.name for tool in runtime.external_tools.specs()] == [
        "mcp.example.inspect"
    ]
    assert all(tool.kind != "skill" for tool in runtime.external_tools.specs())
    assert runtime.external_tool_catalog.get("mcp.example.inspect").name == (
        "mcp.example.inspect"
    )


def test_build_harness_defaults_to_no_model_visible_tools() -> None:
    """Shared implementations are not an implicit Agent toolbox."""
    from restscope.harness import build_harness

    runtime = build_harness()

    assert runtime.external_tools is None
    assert runtime.http_request_tool is not None


def test_retired_capabilities_package_is_not_public_api() -> None:
    """Scenario: Tools, Skills, and Harness have no legacy category facade."""
    import importlib

    import pytest

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("restscope" + ".capabilities")


def test_mcp_package_exports_generic_host_and_adapter_only() -> None:
    """Scenario: verify that mcp package exports generic host and adapter only."""
    import restscope.tools.external.mcp as mcp

    assert mcp.__all__ == [
        "MCPHost",
        "MCPServerConfig",
        "MCPSourceBuilder",
        "MCPToolAdapter",
        "StdioMCPClientSession",
        "load_mcp_server_configs",
    ]
    assert hasattr(mcp, "MCPToolAdapter")
    assert hasattr(mcp, "MCPHost")
    assert not hasattr(mcp, "register_mcp_tools")
    assert not hasattr(mcp, "add_preset_mcp_tools")
