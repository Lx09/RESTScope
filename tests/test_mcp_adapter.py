from __future__ import annotations


def _mcp_tool(name: str, annotations: dict | None, input_schema: dict | None = None) -> dict:
    tool = {
        "name": name,
        "description": f"{name} description",
        "inputSchema": input_schema or {"type": "object"},
    }
    if annotations is not None:
        tool["annotations"] = annotations
    return tool


def test_mcp_tool_adapter_uses_annotations_before_names() -> None:
    from restscope.capabilities.mcp import MCPToolAdapter

    adapter = MCPToolAdapter()

    get_run = adapter.to_tool_spec(
        server_name="schemathesis",
        mcp_tool=_mcp_tool(
            "get_run",
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        ),
    )
    destructive = adapter.to_tool_spec(
        server_name="schemathesis",
        mcp_tool=_mcp_tool(
            "start_run",
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
    missing = adapter.to_tool_spec(server_name="unknown", mcp_tool=_mcp_tool("get_status", None))

    assert get_run.name == "mcp.schemathesis.get_run"
    assert get_run.read_only is True
    assert get_run.requires_approval is False
    assert get_run.risk_level == "low"
    assert get_run.metadata["mcp_annotations"]["readOnlyHint"] is True

    assert destructive.read_only is False
    assert destructive.requires_approval is True
    assert destructive.risk_level == "high"

    assert open_world.read_only is True
    assert open_world.requires_approval is False
    assert open_world.risk_level == "medium"

    assert missing.read_only is False
    assert missing.requires_approval is True
    assert missing.risk_level == "medium"


def test_register_tool_source_uses_external_call_bridge_and_summarizes_results(tool_context) -> None:
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry
    from restscope.capabilities import register_tool_source
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
        server_name="schemathesis",
        source={
            "kind": "mcp",
            "tools": [
                _mcp_tool(
                    "get_result",
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                    input_schema={
                        "type": "object",
                        "properties": {"run_id": {"type": "string"}},
                        "required": ["run_id"],
                    },
                )
            ],
            "call_tool": call_tool,
        },
    )
    executor = ToolExecutor(registry, ToolCallValidator(registry, ToolPolicy()))
    executor.bind_context(tool_context)

    result = executor.execute(
        tool_call=ToolCall(id="call_1", name="mcp.schemathesis.get_result", arguments={"run_id": "run_1"}),
        role="planner",
        state={},
    )

    assert [tool.name for tool in registered] == ["mcp.schemathesis.get_result"]
    assert calls == [("get_result", {"run_id": "run_1"})]
    assert result.status == "succeeded"
    assert result.content is not None
    assert len(result.content) <= 2000
    assert result.structured == {"tool_name": "get_result", "arguments": {"run_id": "run_1"}}
    assert result.artifact_ids == ["artifact_1"]


def test_tool_policy_allows_read_only_mcp_tools_and_denies_unsafe_mcp_tools() -> None:
    from restscope.capabilities import ToolPolicy, ToolRegistry, ToolSelector
    from restscope.capabilities.mcp import MCPToolAdapter

    registry = ToolRegistry()
    adapter = MCPToolAdapter()
    read_only = adapter.to_tool_spec(
        server_name="schemathesis",
        mcp_tool=_mcp_tool(
            "get_failure",
            {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        ),
    )
    unsafe = adapter.to_tool_spec(
        server_name="schemathesis",
        mcp_tool=_mcp_tool(
            "start_run",
            {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        ),
    )
    registry.register(spec=read_only)
    registry.register(spec=unsafe)

    selector = ToolSelector(registry, ToolPolicy())

    assert [tool.name for tool in selector.select_for_role(role="planner", state={})] == [
        "mcp.schemathesis.get_failure"
    ]
    assert [tool.name for tool in selector.select_for_role(role="result_analyst", state={})] == [
        "mcp.schemathesis.get_failure"
    ]
    assert selector.select_for_role(role="check_designer", state={}) == []


def test_add_preset_tools_registers_schemathesis_by_default(tool_context) -> None:
    from restscope.capabilities import ToolCallValidator, ToolExecutor, ToolPolicy, ToolRegistry, add_preset_tools
    from restscope.llm import ToolCall

    calls: list[tuple[str, dict]] = []

    def call_tool(tool_name: str, arguments: dict):
        calls.append((tool_name, arguments))
        return {"content": "result", "structured": {"run_id": arguments["run_id"]}}

    registry = ToolRegistry()
    registered = add_preset_tools(
        registry=registry,
        sources={
            "schemathesis": {
                "kind": "mcp",
                "tools": [
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
                "call_tool": call_tool,
            }
        },
    )
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
    assert result.structured == {"run_id": "run_1"}


def test_add_preset_tools_raises_when_schemathesis_source_is_missing() -> None:
    import pytest

    from restscope.capabilities import PresetToolSourceNotFoundError, ToolRegistry, add_preset_tools

    with pytest.raises(PresetToolSourceNotFoundError, match="Preset tool source not available: schemathesis"):
        add_preset_tools(registry=ToolRegistry(), sources={})


def test_add_preset_tools_rejects_unknown_preset_names() -> None:
    import pytest

    from restscope.capabilities import ToolRegistry, UnsupportedPresetToolSourceError, add_preset_tools

    with pytest.raises(UnsupportedPresetToolSourceError, match="Unsupported preset tool source: unknown"):
        add_preset_tools(
            registry=ToolRegistry(),
            sources={
                "schemathesis": {
                    "kind": "mcp",
                    "tools": [],
                    "call_tool": lambda tool_name, arguments: {},
                }
            },
            presets=("schemathesis", "unknown"),
        )


def test_register_tool_source_rejects_unsupported_source_kind() -> None:
    import pytest

    from restscope.capabilities import ToolRegistry, UnsupportedToolSourceKindError, register_tool_source

    with pytest.raises(UnsupportedToolSourceKindError, match="Unsupported tool source kind: skill"):
        register_tool_source(
            registry=ToolRegistry(),
            server_name="some_skill",
            source={"kind": "skill", "tools": [], "call_tool": lambda tool_name, arguments: {}},
        )


def test_build_capabilities_initializes_tools_and_prompt_only_skills() -> None:
    from restscope.capabilities import SkillManifest, build_capabilities

    calls: list[tuple[str, dict]] = []

    runtime = build_capabilities(
        sources={
            "schemathesis": {
                "kind": "mcp",
                "tools": [
                    _mcp_tool(
                        "get_capabilities",
                        {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "idempotentHint": True,
                            "openWorldHint": False,
                        },
                    )
                ],
                "call_tool": lambda tool_name, arguments: calls.append((tool_name, arguments)) or {"content": "ok"},
            }
        },
        presets=("schemathesis",),
        skills=[
            SkillManifest(
                name="testing_strategy",
                description="Prompt guidance only",
                allowed_roles=["planner"],
                required_tools=["mcp.schemathesis.get_capabilities"],
            )
        ],
    )

    assert runtime.skill_registry.get("testing_strategy").name == "testing_strategy"
    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request",
        "mcp.schemathesis.get_capabilities"
    ]
    assert all(tool.kind != "skill" for tool in runtime.tool_registry.list_specs())
    assert runtime.tool_executor is not None
    assert runtime.skill_policy.is_allowed(
        skill=runtime.skill_registry.get("testing_strategy"),
        role="planner",
        state={},
    )


def test_build_capabilities_requires_default_preset_source() -> None:
    import pytest

    from restscope.capabilities import PresetToolSourceNotFoundError, build_capabilities

    with pytest.raises(PresetToolSourceNotFoundError, match="Preset tool source not available: schemathesis"):
        build_capabilities()


def test_build_capabilities_keeps_builtin_tool_when_presets_are_disabled() -> None:
    from restscope.capabilities import build_capabilities

    runtime = build_capabilities(presets=())

    assert [tool.name for tool in runtime.tool_registry.list_specs()] == [
        "restscope.http.request"
    ]
    assert runtime.tool_executor is not None


def test_default_tools_are_not_public_api() -> None:
    import importlib

    import pytest

    import restscope.capabilities as capabilities

    assert "default_tool_specs" not in capabilities.__all__
    assert not hasattr(capabilities, "default_tool_specs")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("restscope.capabilities.default_tools")


def test_mcp_package_exports_adapter_only() -> None:
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
