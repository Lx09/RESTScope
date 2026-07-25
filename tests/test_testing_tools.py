from __future__ import annotations

from pathlib import Path


def _runtime(tmp_path: Path):
    import httpx

    from restscope.capabilities import ToolContext, build_capabilities
    from restscope.db import Base, SqlAlchemyGeneratorConfigUnitOfWork, create_engine_from_url, make_session_factory
    from restscope.http_transport import TargetHTTPTransport
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog, OperationTestingService

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Tools", "version": "1"},
            "paths": {
                "/status": {
                    "get": {
                        "parameters": [
                            {"name": "verbose", "in": "query", "schema": {"type": "string"}}
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'tools.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(make_session_factory(engine))
    )
    assert catalog.initialize_once(ir) is True
    service = OperationTestingService(
        config_catalog=catalog,
        transport=TargetHTTPTransport(
            client_factory=lambda **kwargs: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, headers={"Content-Type": "text/plain"})
                ),
                **kwargs,
            )
        ),
    )
    runtime = build_capabilities(
        generator_config_catalog=catalog,
        operation_testing_service=service,
    )
    runtime.tool_executor.bind_context(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        )
    )
    return runtime


def test_testing_tools_register_with_run_allowed_for_every_role_and_config_tools_denied(tmp_path: Path) -> None:
    import json

    from restscope.capabilities.testing_tools import (
        INSPECT_INPUTS_TOOL_NAME,
        PATCH_GENERATORS_TOOL_NAME,
        REPLACE_GENERATORS_TOOL_NAME,
        RUN_OPERATION_TOOL_NAME,
    )

    runtime = _runtime(tmp_path)
    run_spec = runtime.tool_registry.get_spec(RUN_OPERATION_TOOL_NAME)
    assert run_spec.risk_level == "high"
    assert run_spec.read_only is False
    assert run_spec.requires_approval is False
    serialized_specs = json.dumps(
        [spec.model_dump(mode="json") for spec in runtime.tool_registry.list_specs()]
    )
    assert "base_url" not in serialized_specs
    assert "schema_source" not in serialized_specs
    assert "Authorization" not in serialized_specs

    config_names = {
        INSPECT_INPUTS_TOOL_NAME,
        REPLACE_GENERATORS_TOOL_NAME,
        PATCH_GENERATORS_TOOL_NAME,
    }
    assert all(
        "trace_arguments" not in runtime.tool_registry.get_spec(name).metadata
        for name in config_names
    )
    for role in (
        "planner",
        "result_analyst",
        "operation_smoke_parameter_diagnosis",
        "decision_maker",
        "openapi_retrieval",
        "future_agent",
    ):
        selected = {
            spec.name
            for spec in runtime.tool_selector.select_for_role(role=role, state={})
        }
        assert RUN_OPERATION_TOOL_NAME in selected
        assert config_names.isdisjoint(selected)


def test_run_operation_tool_returns_the_execution_report(tmp_path: Path) -> None:
    from restscope.capabilities.testing_tools import RUN_OPERATION_TOOL_NAME
    from restscope.llm import ToolCall

    runtime = _runtime(tmp_path)
    result = runtime.tool_executor.execute(
        tool_call=ToolCall(
            id="run-operation",
            name=RUN_OPERATION_TOOL_NAME,
            arguments={"operation_key": "GET /status", "case_count": 1, "seed": 7},
        ),
        role="planner",
        state={},
    )

    assert result.status == "succeeded"
    assert result.structured["operation_key"] == "GET /status"
    assert result.structured["seed"] == 7
    assert result.structured["status"] == "completed"
    assert result.structured["response_validation"] == "not_evaluated"
    assert result.structured["cases"][0]["response"]["status_code"] == 200


def test_default_app_runtime_registers_testing_tools_against_configured_database(
    tmp_path: Path,
) -> None:
    from restscope import RESTScopeApp
    from restscope.capabilities import RUN_OPERATION_TOOL_NAME
    from restscope.restscope_config import RESTScopeConfig
    from tests._operation_smoke_stub import PassingOperationSmokeAgent

    env_file = tmp_path / ".env"
    env_file.write_text(
        f"DATA_DIR={tmp_path / 'data'}\nDB_URL=sqlite:///{tmp_path / 'app.sqlite'}\n",
        encoding="utf-8",
    )
    app = RESTScopeApp.from_config(
        RESTScopeConfig.from_environment(env_file),
        operation_smoke_agent=PassingOperationSmokeAgent(),
    )
    try:
        assert app.capability_runtime.tool_registry.get_spec(RUN_OPERATION_TOOL_NAME).name == (
            RUN_OPERATION_TOOL_NAME
        )
    finally:
        app.close()


def test_configuration_tools_patch_and_return_complete_generator_values(tmp_path: Path) -> None:
    from restscope.capabilities.testing_tools import (
        INSPECT_INPUTS_TOOL_NAME,
        PATCH_GENERATORS_TOOL_NAME,
        REPLACE_GENERATORS_TOOL_NAME,
    )

    runtime = _runtime(tmp_path)
    context = runtime.tool_executor.require_context()
    original = runtime.tool_registry.get_handler(INSPECT_INPUTS_TOOL_NAME)(
        context,
        operation_key="GET /status",
    )
    node = original["structured"]["snapshot"]["input_nodes"][0]
    configured_value = "configured-value-visible-to-management"
    patched = runtime.tool_registry.get_handler(PATCH_GENERATORS_TOOL_NAME)(
        context,
        operation_key="GET /status",
        expected_revision=1,
        updates=[
            {
                "input_node_id": node["input_node_id"],
                "inclusion_probability": 1,
                "strategy": {"type": "constant", "value": configured_value},
            }
        ],
    )
    inspection = runtime.tool_registry.get_handler(INSPECT_INPUTS_TOOL_NAME)(
        context,
        operation_key="GET /status",
    )
    replacement = runtime.tool_registry.get_handler(REPLACE_GENERATORS_TOOL_NAME)(
        context,
        operation_key="GET /status",
        expected_revision=2,
        active_media_type=None,
        configs=inspection["structured"]["configs"],
    )

    assert patched["structured"]["revision"] == 2
    assert replacement["structured"]["revision"] == 3
    assert configured_value in repr(patched)
    assert configured_value in repr(inspection)
    assert patched["structured"]["configs"][0]["strategy"] == {
        "type": "constant",
        "value": configured_value,
    }
    assert inspection["structured"]["enabled"] is True
    assert inspection["structured"]["disabled_reasons"] == []
    assert inspection["structured"]["snapshot"]["available_media_types"] == []


def test_configuration_tools_use_frozen_catalog_when_current_ir_changes(tmp_path: Path) -> None:
    from restscope.capabilities import ToolContext
    from restscope.capabilities.testing_tools import INSPECT_INPUTS_TOOL_NAME
    from restscope.openapi_parser import OpenAPIParser

    runtime = _runtime(tmp_path)
    changed_ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Changed", "version": "2"},
            "paths": {
                "/different": {
                    "post": {"responses": {"204": {"description": "done"}}}
                }
            },
        }
    )
    runtime.tool_executor.clear_context()
    runtime.tool_executor.bind_context(
        ToolContext(
            ir=changed_ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        )
    )

    inspection = runtime.tool_registry.get_handler(INSPECT_INPUTS_TOOL_NAME)(
        runtime.tool_executor.require_context(),
        operation_key="GET /status",
    )

    assert inspection["structured"]["snapshot"]["method"] == "GET"
    assert inspection["structured"]["snapshot"]["path"] == "/status"


def test_delete_generator_tool_is_not_registered(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    names = {spec.name for spec in runtime.tool_registry.list_specs()}

    assert "restscope.testing.delete_operation_generators" not in names
