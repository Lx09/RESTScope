"""Regression scenarios for restart cleanup. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_top_level_package_exports_only_app_facing_types() -> None:
    """The package root stays a side-effect-free App-facing facade."""
    restscope = importlib.import_module("restscope")

    assert set(restscope.__all__) == {"RESTScopeApp", "RESTScopeConfig"}
    assert hasattr(restscope, "RESTScopeApp")
    assert hasattr(restscope, "RESTScopeConfig")
    assert not hasattr(restscope, "OpenAPIParser")
    assert not hasattr(restscope, "ToolContext")
    assert not hasattr(restscope, "set_workflow_context")


def test_flat_environment_names_configure_logging(monkeypatch, tmp_path: Path) -> None:
    """Scenario: verify that flat environment names configure logging."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATA_DIR", "/tmp/restscope-data")
    monkeypatch.delenv("RESTSCOPE_LOGGING__LEVEL", raising=False)
    monkeypatch.delenv("RESTSCOPE_PATHS__DATA_DIR", raising=False)

    config_module = importlib.import_module("restscope.config")
    config = config_module.RESTScopeConfig.from_environment(tmp_path / ".env")

    assert config.logging.level == "DEBUG"
    assert config.paths.data_dir == Path("/tmp/restscope-data")


def test_model_file_configures_three_named_models(monkeypatch, tmp_path: Path) -> None:
    """One Provider connection can back any number of named model settings."""
    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.openai_compatible]\n"
        'api_key_env = "COMPAT_API_KEY"\n'
        'url = "https://models.example/v1"\n'
        "\n"
        "[models.default]\n"
        'provider = "openai_compatible"\n'
        'model = "general-model"\n'
        "\n"
        "[models.quick]\n"
        'provider = "openai_compatible"\n'
        'model = "quick-model"\n'
        "timeout = 30\n"
        "temperature = 0.2\n"
        "max_tokens = 4096\n"
        "\n"
        "[models.large-context]\n"
        'provider = "openai_compatible"\n'
        'model = "large-model"\n'
        "context_tokens = 200000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODELS_FILE", str(models_file))
    monkeypatch.setenv("COMPAT_API_KEY", "provider-key")

    config_module = importlib.import_module("restscope.config")
    config = config_module.RESTScopeConfig.from_environment(tmp_path / ".env")

    assert [model.name for model in config.llm.models] == [
        "default",
        "quick",
        "large-context",
    ]
    assert config.llm.providers[0].api_key == "provider-key"
    assert config.llm.models[1].timeout == 30
    assert config.llm.models[1].temperature == 0.2
    assert config.llm.models[1].max_tokens == 4096
    assert config.llm.models[2].context_window_tokens == 200000


def test_short_environment_name_configures_mcp_servers_file(
    monkeypatch, tmp_path
) -> None:
    """Scenario: verify that short environment name configures mcp servers file."""
    servers_file = tmp_path / "mcp.servers.json"
    monkeypatch.setenv("MCP_FILE", str(servers_file))

    config_module = importlib.import_module("restscope.config")
    config = config_module.RESTScopeConfig.from_environment(tmp_path / ".env")

    assert config.mcp.servers_file == servers_file


def test_parser_only_config_does_not_create_an_agent_runtime(tmp_path: Path) -> None:
    """Omitting MODELS_FILE keeps parsing usable without a Provider secret."""
    from restscope.app.profiles import _build_agent_runtime_definition
    from restscope.config import RESTScopeConfig
    from restscope.observability import TracingRuntime

    config = RESTScopeConfig.from_environment(tmp_path / "missing.env")

    assert config.llm.providers == ()
    assert config.llm.models == ()
    assert (
        _build_agent_runtime_definition(
            config,
            tracing_runtime=TracingRuntime.disabled(),
            test_progress_context=None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("catalog", "error"),
    (
        (
            "unexpected = true\n",
            "model catalog contains unknown field: unexpected",
        ),
        (
            (
                "[providers.deepseek]\n"
                'api_key_env = "MODEL_KEY"\n'
                "unexpected = true\n"
                "[models.default]\n"
                'provider = "deepseek"\n'
                'model = "model-id"\n'
            ),
            "providers.deepseek contains unknown field: unexpected",
        ),
        (
            (
                "[providers.deepseek]\n"
                'api_key_env = "MODEL_KEY"\n'
                "[models.default]\n"
                'provider = "deepseek"\n'
                'model = "model-id"\n'
                "unexpected = true\n"
            ),
            "models.default contains unknown field: unexpected",
        ),
    ),
)
def test_model_file_rejects_unknown_fields(
    catalog: str,
    error: str,
    tmp_path: Path,
) -> None:
    """Misspelled TOML fields fail instead of silently selecting defaults."""
    from restscope.config import RESTScopeConfig

    models_file = tmp_path / "models.toml"
    models_file.write_text(catalog, encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nMODEL_KEY=test-key\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error):
        RESTScopeConfig.from_environment(env_file)


def test_model_file_rejects_duplicate_definitions(tmp_path: Path) -> None:
    """Duplicate TOML model tables are an unambiguous startup error."""
    from restscope.config import RESTScopeConfig

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        '[models.default]\nmodel = "first"\n[models.default]\nmodel = "second"\n',
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"MODELS_FILE={models_file}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="could not be read as TOML"):
        RESTScopeConfig.from_environment(env_file)


@pytest.mark.parametrize(
    ("catalog", "error"),
    (
        (
            '[providers.custom]\napi_key_env = "MODEL_KEY"\n',
            "unsupported Provider.*custom",
        ),
        (
            (
                "[providers.openai_compatible]\n"
                'api_key_env = "MODEL_KEY"\n'
                "[models.default]\n"
                'provider = "deepseek"\n'
                'model = "model-id"\n'
            ),
            "models.default.provider is not configured: deepseek",
        ),
        (
            (
                "[providers.deepseek]\n"
                'api_key_env = "MODEL_KEY"\n'
                "[models.BadName]\n"
                'provider = "deepseek"\n'
                'model = "model-id"\n'
            ),
            "model configuration name",
        ),
    ),
)
def test_model_file_rejects_unknown_references_and_invalid_names(
    catalog: str,
    error: str,
    tmp_path: Path,
) -> None:
    """Provider and model names are validated before runtime registration."""
    from restscope.config import RESTScopeConfig

    models_file = tmp_path / "models.toml"
    models_file.write_text(catalog, encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nMODEL_KEY=test-key\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error):
        RESTScopeConfig.from_environment(env_file)


def test_model_file_requires_its_secret_and_at_least_one_model(
    tmp_path: Path,
) -> None:
    """A configured runtime cannot start with a blank secret or empty catalog."""
    from restscope.config import RESTScopeConfig

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        '[providers.deepseek]\napi_key_env = "MISSING_MODEL_KEY"\n',
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(f"MODELS_FILE={models_file}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing or empty.*MISSING_MODEL_KEY"):
        RESTScopeConfig.from_environment(env_file)

    env_file.write_text(
        f"MODELS_FILE={models_file}\nMISSING_MODEL_KEY=test-key\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one named model"):
        RESTScopeConfig.from_environment(env_file)


def test_models_file_must_exist_and_relative_paths_use_process_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """MODELS_FILE is mandatory when named and resolves from the startup cwd."""
    from restscope.config import RESTScopeConfig

    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MODELS_FILE=./models.toml\nMODEL_KEY=test-key\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="does not name a readable file"):
        RESTScopeConfig.from_environment(env_file)

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.deepseek]\n"
        'api_key_env = "MODEL_KEY"\n'
        "[models.default]\n"
        'provider = "deepseek"\n'
        'model = "model-id"\n',
        encoding="utf-8",
    )
    assert RESTScopeConfig.from_environment(env_file).llm.models[0].name == "default"


def test_process_environment_overrides_dotenv_secrets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Provider secrets follow the existing process-over-dotenv precedence."""
    from restscope.config import RESTScopeConfig

    models_file = tmp_path / "models.toml"
    models_file.write_text(
        "[providers.deepseek]\n"
        'api_key_env = "MODEL_KEY"\n'
        "[models.default]\n"
        'provider = "deepseek"\n'
        'model = "model-id"\n',
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MODELS_FILE={models_file}\nMODEL_KEY=file-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_KEY", "process-secret")

    config = RESTScopeConfig.from_environment(env_file)

    assert config.llm.providers[0].api_key == "process-secret"


def test_all_short_app_environment_names_are_loaded(tmp_path: Path) -> None:
    """The concise App configuration surface maps to every owned setting."""
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATA_DIR=./runtime-data\n"
        "LOG_LEVEL=DEBUG\n"
        "LOG_FILE=./runtime.log\n"
        "LIB_LOG_LEVEL=ERROR\n"
        "DB_URL=sqlite:///short.sqlite\n"
        "DB_SQL_LOG=true\n"
        "DB_POOL_SIZE=4\n"
        "DB_POOL_EXTRA=6\n"
        "MCP_FILE=./servers.json\n"
        "RUN_SEED=731\n"
        "UI_ON=true\n"
        "UI_PORT=9876\n"
        "TRACE_ON=true\n"
        "TRACE_URL=http://trace.test:6006\n"
        "TRACE_PROJECT=short-names\n"
        "TRACE_API_KEY=trace-secret\n"
        "TRACE_PROTOCOL=http/protobuf\n"
        "TRACE_BATCH=false\n"
        "TRACE_MAX_BYTES=2048\n"
        "TRACE_FLUSH_TIMEOUT=2.5\n",
        encoding="utf-8",
    )

    config = RESTScopeConfig.from_environment(env_file)

    assert config.paths.data_dir == Path("runtime-data")
    assert config.logging.level == "DEBUG"
    assert config.logging.file == Path("runtime.log")
    assert config.logging.third_party_level == "ERROR"
    assert config.db.url == "sqlite:///short.sqlite"
    assert config.db.echo is True
    assert config.db.pool_size == 4
    assert config.db.max_overflow == 6
    assert config.mcp.servers_file == Path("servers.json")
    assert config.random.seed == 731
    assert config.ui.enabled is True
    assert config.ui.port == 9876
    assert config.tracing.enabled is True
    assert config.tracing.collector_endpoint == "http://trace.test:6006"
    assert config.tracing.project_name == "short-names"
    assert config.tracing.api_key == "trace-secret"
    assert config.tracing.batch is False
    assert config.tracing.max_content_bytes == 2048
    assert config.tracing.flush_timeout_seconds == 2.5


@pytest.mark.parametrize(
    ("old_name", "replacement"),
    (
        ("THIRD_PARTY_LOG_LEVEL", "LIB_LOG_LEVEL"),
        ("DB_ECHO", "DB_SQL_LOG"),
        ("DB_MAX_OVERFLOW", "DB_POOL_EXTRA"),
        ("MCP_SERVERS_FILE", "MCP_FILE"),
        ("RANDOM_SEED", "RUN_SEED"),
        ("UI_ENABLED", "UI_ON"),
        ("TRACING_ENABLED", "TRACE_ON"),
        ("PHOENIX_COLLECTOR_ENDPOINT", "TRACE_URL"),
        ("PHOENIX_PROJECT_NAME", "TRACE_PROJECT"),
        ("PHOENIX_API_KEY", "TRACE_API_KEY"),
        ("PHOENIX_PROTOCOL", "TRACE_PROTOCOL"),
        ("TRACING_BATCH", "TRACE_BATCH"),
        ("TRACING_MAX_CONTENT_BYTES", "TRACE_MAX_BYTES"),
        ("TRACING_FLUSH_TIMEOUT_SECONDS", "TRACE_FLUSH_TIMEOUT"),
        ("THINK_PROVIDER", "MODELS_FILE"),
        ("THINK_MODEL", "MODELS_FILE"),
        ("THINK_API_KEY", "api_key_env"),
        ("THINK_BASE_URL", "MODELS_FILE"),
        ("THINK_TIMEOUT", "MODELS_FILE"),
        ("THINK_TEMPERATURE", "MODELS_FILE"),
        ("THINK_MAX_TOKENS", "MODELS_FILE"),
        ("THINK_CONTEXT_WINDOW_TOKENS", "MODELS_FILE"),
        ("THINK_REASONING_MODE", "AgentProfile.reasoning_effort"),
        ("THINK_REASONING_EFFORT", "AgentProfile.reasoning_effort"),
        ("FAST_PROVIDER", "MODELS_FILE"),
        ("FAST_MODEL", "MODELS_FILE"),
        ("FAST_API_KEY", "api_key_env"),
        ("FAST_BASE_URL", "MODELS_FILE"),
        ("FAST_TIMEOUT", "MODELS_FILE"),
        ("FAST_TEMPERATURE", "MODELS_FILE"),
        ("FAST_MAX_TOKENS", "MODELS_FILE"),
        ("FAST_CONTEXT_WINDOW_TOKENS", "MODELS_FILE"),
        ("FAST_REASONING_MODE", "AgentProfile.reasoning_effort"),
        ("FAST_REASONING_EFFORT", "AgentProfile.reasoning_effort"),
    ),
)
def test_retired_environment_names_fail_with_their_migration_target(
    old_name: str,
    replacement: str,
    tmp_path: Path,
) -> None:
    """A retired field never acts as an alias and identifies its replacement."""
    from restscope.config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(f"{old_name}=old-value\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"{old_name}.*{replacement}"):
        RESTScopeConfig.from_environment(env_file)


def test_parser_loads_bundled_petstore_spec() -> None:
    """Scenario: verify that parser loads bundled petstore spec."""
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser().parse("assets/openapi/petstore-v3.json")

    assert ir.meta.title
    assert ir.operations
