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


def test_mcp_tool_adapter_uses_annotations_before_names() -> None:
    """Scenario: verify that mcp tool adapter uses annotations before names."""
    from restscope.capabilities.mcp import MCPToolAdapter

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
    assert read_only.read_only is True
    assert read_only.requires_approval is False
    assert read_only.risk_level == "low"
    assert read_only.metadata["mcp_annotations"]["readOnlyHint"] is True
    assert destructive.read_only is False
    assert destructive.requires_approval is True
    assert destructive.risk_level == "high"
    assert open_world.read_only is True
    assert open_world.requires_approval is False
    assert open_world.risk_level == "medium"
    assert missing.read_only is False
    assert missing.requires_approval is True
    assert missing.risk_level == "medium"


def test_register_tool_source_uses_external_call_bridge_and_summarizes_results(
    tool_context,
) -> None:
    """Scenario: verify that register tool source uses external call bridge and summarizes results."""
    from restscope.capabilities import (
        ToolCallValidator,
        ToolExecutor,
        ToolPolicy,
        ToolRegistry,
        register_tool_source,
    )
    from restscope.llm import ToolCall

    calls: list[tuple[str, dict]] = []

    def call_tool(tool_name: str, arguments: dict):
        calls.append((tool_name, arguments))
        return {
            "content": "x" * 2500,
            "structured": {"tool_name": tool_name, "arguments": arguments},
            "artifact_ids": ["artifact_1"],
        }

    registry = ToolRegistry()
    registered = register_tool_source(
        registry=registry,
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
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    executor.bind_context(tool_context)

    result = executor.execute(
        tool_call=ToolCall(
            id="call_1",
            name="mcp.example.inspect",
            arguments={"item_id": "item_1"},
        ),
        role="planner",
        state={},
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


def test_tool_policy_allows_generic_read_only_mcp_tools_and_denies_unsafe_tools() -> None:
    """Scenario: verify that tool policy allows generic read only mcp tools and denies unsafe tools."""
    from restscope.capabilities import ToolPolicy, ToolRegistry, ToolSelector
    from restscope.capabilities.mcp import MCPToolAdapter

    registry = ToolRegistry()
    adapter = MCPToolAdapter()
    registry.register(
        spec=adapter.to_tool_spec(
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
    )
    registry.register(
        spec=adapter.to_tool_spec(
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
    )

    selector = ToolSelector(registry, ToolPolicy())

    assert [
        tool.name
        for tool in selector.select_for_role(role="planner", state={})
    ] == ["mcp.example.inspect"]
    assert [
        tool.name
        for tool in selector.select_for_role(role="result_analyst", state={})
    ] == ["mcp.example.inspect"]
    assert selector.select_for_role(role="check_designer", state={}) == []


def test_register_tool_source_rejects_unsupported_source_kind() -> None:
    """Scenario: verify that register tool source rejects unsupported source kind."""
    import pytest

    from restscope.capabilities import (
        ToolRegistry,
        UnsupportedToolSourceKindError,
        register_tool_source,
    )

    with pytest.raises(
        UnsupportedToolSourceKindError,
        match="Unsupported tool source kind: skill",
    ):
        register_tool_source(
            registry=ToolRegistry(),
            server_name="some_skill",
            source={
                "kind": "skill",
                "tools": [],
                "call_tool": lambda tool_name, arguments: {},
            },
        )


def test_build_capabilities_registers_all_explicit_sources_without_presets() -> None:
    """Scenario: verify that build capabilities registers all explicit sources without presets."""
    import inspect

    import restscope.capabilities as capabilities

    runtime = capabilities.build_capabilities(
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

    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request",
        "openapi.lookup_operation",
        "mcp.example.inspect",
    ]
    assert "presets" not in inspect.signature(
        capabilities.build_capabilities
    ).parameters
    assert not hasattr(capabilities, "add_preset_tools")


def test_build_capabilities_initializes_tools_and_prompt_only_skills() -> None:
    """Scenario: verify that build capabilities initializes tools and prompt only skills."""
    from restscope.capabilities import SkillManifest, build_capabilities

    runtime = build_capabilities(
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
        skills=[
            SkillManifest(
                name="testing_strategy",
                description="Prompt guidance only",
                allowed_roles=["planner"],
                required_tools=["mcp.example.inspect"],
            )
        ],
    )

    assert runtime.skill_registry.get("testing_strategy").name == "testing_strategy"
    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request",
        "openapi.lookup_operation",
        "mcp.example.inspect",
    ]
    assert all(tool.kind != "skill" for tool in runtime.tool_registry.list_specs())
    assert runtime.tool_executor is not None
    assert runtime.skill_policy.is_allowed(
        skill=runtime.skill_registry.get("testing_strategy"),
        role="planner",
        state={},
    )


def test_build_capabilities_defaults_to_builtin_tools_only() -> None:
    """Scenario: verify that build capabilities defaults to builtin tools only."""
    from restscope.capabilities import build_capabilities

    runtime = build_capabilities()

    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request",
        "openapi.lookup_operation",
    ]
    assert runtime.tool_executor is not None


def test_default_tools_are_not_public_api() -> None:
    """Scenario: verify that default tools are not public api."""
    import importlib

    import pytest

    import restscope.capabilities as capabilities

    assert "default_tool_specs" not in capabilities.__all__
    assert not hasattr(capabilities, "default_tool_specs")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("restscope.capabilities.default_tools")


def test_mcp_package_exports_generic_host_and_adapter_only() -> None:
    """Scenario: verify that mcp package exports generic host and adapter only."""
    import restscope.capabilities.mcp as mcp

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
