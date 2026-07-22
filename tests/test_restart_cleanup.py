from __future__ import annotations

import importlib
from pathlib import Path


def test_top_level_package_exports_parser_only_runtime() -> None:
    restscope = importlib.import_module("restscope")

    assert hasattr(restscope, "OpenAPIParser")
    assert hasattr(restscope, "OpenAPISpecIR")
    assert not hasattr(restscope, "ToolContext")
    assert not hasattr(restscope, "set_workflow_context")


def test_flat_environment_names_configure_logging(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATA_DIR", "/tmp/restscope-data")
    monkeypatch.delenv("RESTSCOPE_LOGGING__LEVEL", raising=False)
    monkeypatch.delenv("RESTSCOPE_PATHS__DATA_DIR", raising=False)

    config_module = importlib.import_module("restscope.restscope_config")
    config = config_module.RESTScopeConfig.from_environment()

    assert config.logging.level == "DEBUG"
    assert config.paths.data_dir == Path("/tmp/restscope-data")


def test_short_environment_names_configure_dual_llm_models(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THINK_MODEL", "glm-4.5-air")
    monkeypatch.setenv("THINK_API_KEY", "think-key")
    monkeypatch.setenv("THINK_BASE_URL", "https://think.example/v1")
    monkeypatch.setenv("FAST_MODEL", "glm-4.7-flash")
    monkeypatch.setenv("FAST_TIMEOUT", "30")
    monkeypatch.setenv("FAST_TEMPERATURE", "0.2")
    monkeypatch.setenv("FAST_MAX_TOKENS", "4096")
    monkeypatch.delenv("FAST_API_KEY", raising=False)
    monkeypatch.delenv("FAST_BASE_URL", raising=False)

    config_module = importlib.import_module("restscope.restscope_config")
    config = config_module.RESTScopeConfig.from_environment(tmp_path / ".env")

    assert config.llm.thinking.model == "glm-4.5-air"
    assert config.llm.thinking.api_key == "think-key"
    assert config.llm.thinking.base_url == "https://think.example/v1"
    assert config.llm.fast.model == "glm-4.7-flash"
    assert config.llm.fast.api_key == "think-key"
    assert config.llm.fast.base_url == "https://think.example/v1"
    assert config.llm.fast.timeout == 30
    assert config.llm.fast.temperature == 0.2
    assert config.llm.fast.max_tokens == 4096


def test_short_environment_name_configures_mcp_servers_file(monkeypatch, tmp_path) -> None:
    servers_file = tmp_path / "mcp.servers.json"
    monkeypatch.setenv("MCP_SERVERS_FILE", str(servers_file))

    config_module = importlib.import_module("restscope.restscope_config")
    config = config_module.RESTScopeConfig.from_environment()

    assert config.mcp.servers_file == servers_file


def test_parser_loads_bundled_petstore_spec() -> None:
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser().parse("assets/openapi/petstore-v3.json")

    assert ir.meta.title
    assert ir.operations
