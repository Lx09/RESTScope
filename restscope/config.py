"""Load explicit typed configuration for one RESTScope App lifetime.

The Module reads an optional dotenv file plus the process environment only when
``RESTScopeConfig.from_environment`` is called. Importing it creates no
directories, log files, database connections, or runtime objects.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE lines from a local .env file."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _merged_environment(env_file: Path) -> dict[str, str]:
    file_values = _load_env_file(env_file)
    return {**file_values, **os.environ}


@dataclass(frozen=True)
class PathsConfig:
    """Filesystem paths used by parser-adjacent utilities."""

    data_dir: Path = PROJECT_ROOT / "data"

    @property
    def logs_dir_resolved(self) -> Path:
        """Return the configured log directory without touching the filesystem."""
        return self.data_dir / "logs"


@dataclass(frozen=True)
class LoggingConfig:
    """Logging settings controlled by short environment names."""

    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Path | None = None
    third_party_level: str = "WARNING"


ProviderName = Literal["deepseek", "openai_compatible"]
_CONFIG_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_LEGACY_ENV_REPLACEMENTS = {
    "THIRD_PARTY_LOG_LEVEL": "LIB_LOG_LEVEL",
    "DB_ECHO": "DB_SQL_LOG",
    "DB_MAX_OVERFLOW": "DB_POOL_EXTRA",
    "MCP_SERVERS_FILE": "MCP_FILE",
    "RANDOM_SEED": "RUN_SEED",
    "UI_ENABLED": "UI_ON",
    "TRACING_ENABLED": "TRACE_ON",
    "PHOENIX_COLLECTOR_ENDPOINT": "TRACE_URL",
    "PHOENIX_PROJECT_NAME": "TRACE_PROJECT",
    "PHOENIX_API_KEY": "TRACE_API_KEY",
    "PHOENIX_PROTOCOL": "TRACE_PROTOCOL",
    "TRACING_BATCH": "TRACE_BATCH",
    "TRACING_MAX_CONTENT_BYTES": "TRACE_MAX_BYTES",
    "TRACING_FLUSH_TIMEOUT_SECONDS": "TRACE_FLUSH_TIMEOUT",
}
for _slot in ("THINK", "FAST"):
    for _field in (
        "PROVIDER",
        "MODEL",
        "BASE_URL",
        "TIMEOUT",
        "TEMPERATURE",
        "MAX_TOKENS",
        "CONTEXT_WINDOW_TOKENS",
    ):
        _LEGACY_ENV_REPLACEMENTS[f"{_slot}_{_field}"] = "MODELS_FILE"
    _LEGACY_ENV_REPLACEMENTS[f"{_slot}_API_KEY"] = (
        "the Provider api_key_env named by MODELS_FILE"
    )
    for _field in ("REASONING_MODE", "REASONING_EFFORT"):
        _LEGACY_ENV_REPLACEMENTS[f"{_slot}_{_field}"] = "AgentProfile.reasoning_effort"


@dataclass(frozen=True)
class ProviderConfig:
    """One configured Provider connection shared by named models.

    Args:
        name: Existing RESTScope Provider Adapter to register.
        api_key: Secret resolved from the environment variable named in TOML.
        base_url: Optional endpoint override. An empty value selects the
            Provider Adapter's existing default.
    """

    name: ProviderName
    api_key: str = field(repr=False)
    base_url: str = ""


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for one LLM endpoint.

    ``context_window_tokens`` is the model's total request capacity, while
    ``max_tokens`` is reserved for one response. Keeping them separate lets
    prompt builders use the remaining space for evidence without relying on a
    provider-specific model registry.
    """

    name: str = ""
    provider: ProviderName = "openai_compatible"
    model: str = ""
    timeout: int = 60
    temperature: float = 0.7
    max_tokens: int = 8192
    context_window_tokens: int = 131072

    def __post_init__(self) -> None:
        """Reject invalid names and a response reservation with no input room."""
        if len(self.name) > 120 or not _CONFIG_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                "model configuration name must be 1-120 characters, start "
                "with a lowercase letter, and contain only lowercase letters, "
                "digits, dots, hyphens, or underscores"
            )
        if not self.model.strip():
            raise ValueError(f"model configuration {self.name} must name a model")
        if self.max_tokens >= self.context_window_tokens:
            raise ValueError("max_tokens must be smaller than context_window_tokens")


@dataclass(frozen=True)
class LLMConfig:
    """Validated Provider connections and arbitrarily named model settings."""

    providers: tuple[ProviderConfig, ...] = ()
    models: tuple[ModelConfig, ...] = ()

    @property
    def api_keys(self) -> tuple[str, ...]:
        """Return every configured Provider secret for redaction registration."""
        return tuple(provider.api_key for provider in self.providers)


@dataclass(frozen=True)
class DBConfig:
    """Database connection configuration."""

    url: str = "sqlite:///./data/restscope.db"
    echo: bool = False
    pool_size: int | None = None
    max_overflow: int | None = None


@dataclass(frozen=True)
class RandomConfig:
    """Optional root seed shared by all generated test values in one App."""

    seed: int | None = None

    def __post_init__(self) -> None:
        """Reject negative seeds before any runtime is constructed."""
        if self.seed is not None and self.seed < 0:
            raise ValueError("RUN_SEED must be non-negative")


@dataclass(frozen=True)
class MCPConfig:
    """MCP host configuration."""

    servers_file: Path = PROJECT_ROOT / "mcp.servers.json"


@dataclass(frozen=True)
class TracingConfig:
    """Optional Phoenix/OpenTelemetry export settings."""

    enabled: bool = False
    collector_endpoint: str = "http://localhost:6006"
    project_name: str = "restscope"
    api_key: str = field(default="", repr=False)
    protocol: str = "http/protobuf"
    batch: bool = True
    max_content_bytes: int = 65536
    flush_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class UIConfig:
    """Optional loopback-only live observer settings.

    Args:
        enabled: Whether the App should collect and serve current-run events.
        port: Fixed local TCP port. The host is intentionally not configurable
            because v1 displays target credentials without viewer authentication.
    """

    enabled: bool = False
    port: int = 8765

    def __post_init__(self) -> None:
        """Reject ports that cannot be bound by a TCP server."""
        if not 1 <= self.port <= 65535:
            raise ValueError("UI_PORT must be between 1 and 65535")


@dataclass(frozen=True)
class RESTScopeConfig:
    """RESTScope configuration loaded from `.env` and environment variables."""

    paths: PathsConfig
    logging: LoggingConfig
    llm: LLMConfig
    db: DBConfig
    mcp: MCPConfig
    tracing: TracingConfig = field(default_factory=TracingConfig)
    random: RandomConfig = field(default_factory=RandomConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> RESTScopeConfig:
        """Load and validate one complete process configuration.

        Values from the process environment override the optional dotenv file.
        Invalid App fields, model catalogs, Provider references, and secrets
        fail here before any runtime service is opened.
        """
        values = _merged_environment(env_file or PROJECT_ROOT / ".env")
        _reject_legacy_environment(values)
        data_dir = Path(values.get("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
        log_file = values.get("LOG_FILE")
        llm = _load_llm_config(values)

        return cls(
            paths=PathsConfig(data_dir=data_dir),
            logging=LoggingConfig(
                level=values.get("LOG_LEVEL", "INFO"),
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                file=Path(log_file).expanduser() if log_file else None,
                third_party_level=values.get("LIB_LOG_LEVEL", "WARNING"),
            ),
            llm=llm,
            db=DBConfig(
                url=values.get("DB_URL", "sqlite:///./data/restscope.db"),
                echo=_bool_value(values.get("DB_SQL_LOG"), False),
                pool_size=_optional_int_value(values.get("DB_POOL_SIZE")),
                max_overflow=_optional_int_value(values.get("DB_POOL_EXTRA")),
            ),
            mcp=MCPConfig(
                servers_file=Path(
                    values.get("MCP_FILE", str(PROJECT_ROOT / "mcp.servers.json"))
                ).expanduser(),
            ),
            tracing=TracingConfig(
                enabled=_bool_value(values.get("TRACE_ON"), False),
                collector_endpoint=values.get(
                    "TRACE_URL",
                    "http://localhost:6006",
                ),
                project_name=values.get("TRACE_PROJECT", "restscope"),
                api_key=values.get("TRACE_API_KEY", ""),
                protocol=values.get("TRACE_PROTOCOL", "http/protobuf"),
                batch=_bool_value(values.get("TRACE_BATCH"), True),
                max_content_bytes=_int_value(
                    values.get("TRACE_MAX_BYTES"),
                    65536,
                ),
                flush_timeout_seconds=_float_value(
                    values.get("TRACE_FLUSH_TIMEOUT"),
                    5.0,
                ),
            ),
            random=RandomConfig(
                seed=_optional_int_value(values.get("RUN_SEED")),
            ),
            ui=UIConfig(
                enabled=_bool_value(values.get("UI_ON"), False),
                port=_int_value(values.get("UI_PORT"), 8765),
            ),
        )

    @property
    def log_file(self) -> Path:
        """Return the configured or data-directory log path without writing it."""
        return self.logging.file or self.paths.logs_dir_resolved / "restscope.log"


def _load_llm_config(values: dict[str, str]) -> LLMConfig:
    """Load one optional closed TOML catalog and resolve Provider secrets.

    The model file is an explicit startup input. Omitting ``MODELS_FILE`` keeps
    parser-only use free of model configuration and network credentials.
    Relative paths intentionally follow the process startup directory, matching
    the other caller-supplied local paths in this Module.
    """
    configured_path = values.get("MODELS_FILE", "").strip()
    if not configured_path:
        return LLMConfig()
    path = Path(configured_path).expanduser()
    if not path.is_file():
        raise ValueError(f"MODELS_FILE does not name a readable file: {path}")
    try:
        raw_catalog: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"MODELS_FILE could not be read as TOML: {path}") from exc

    root = _object_table(raw_catalog, location="model catalog")
    _reject_unknown_keys(root, {"providers", "models"}, location="model catalog")
    provider_rows = _object_table(root.get("providers", {}), location="providers")
    model_rows = _object_table(root.get("models", {}), location="models")

    providers = tuple(
        _load_provider_config(name, row, values=values)
        for name, row in provider_rows.items()
    )
    provider_names = {provider.name for provider in providers}
    models = tuple(
        _load_catalog_model(name, row, provider_names=provider_names)
        for name, row in model_rows.items()
    )
    if not models:
        raise ValueError("MODELS_FILE must define at least one named model")
    return LLMConfig(providers=providers, models=models)


def _reject_legacy_environment(values: dict[str, str]) -> None:
    """Stop on one retired key so configuration never falls back silently."""
    for old_name in sorted(_LEGACY_ENV_REPLACEMENTS):
        if old_name in values:
            replacement = _LEGACY_ENV_REPLACEMENTS[old_name]
            raise ValueError(
                f"environment field {old_name} was removed; use {replacement}"
            )


def _load_provider_config(
    name: str,
    raw: object,
    *,
    values: dict[str, str],
) -> ProviderConfig:
    """Validate one supported Provider table and resolve its named secret."""
    if name not in {"deepseek", "openai_compatible"}:
        raise ValueError(f"unsupported Provider in MODELS_FILE: {name}")
    row = _object_table(raw, location=f"providers.{name}")
    _reject_unknown_keys(row, {"api_key_env", "url"}, location=f"providers.{name}")
    api_key_env = _required_string(row, "api_key_env", location=f"providers.{name}")
    api_key = values.get(api_key_env, "").strip()
    if not api_key:
        raise ValueError(
            f"providers.{name}.api_key_env names missing or empty environment "
            f"variable: {api_key_env}"
        )
    url = _optional_string(row, "url", location=f"providers.{name}")
    return ProviderConfig(name=name, api_key=api_key, base_url=url or "")


def _load_catalog_model(
    name: str,
    raw: object,
    *,
    provider_names: set[ProviderName],
) -> ModelConfig:
    """Validate one named model and its reference to a configured Provider."""
    row = _object_table(raw, location=f"models.{name}")
    _reject_unknown_keys(
        row,
        {"provider", "model", "timeout", "temperature", "max_tokens", "context_tokens"},
        location=f"models.{name}",
    )
    provider_value = _required_string(row, "provider", location=f"models.{name}")
    if provider_value not in {"deepseek", "openai_compatible"}:
        raise ValueError(f"models.{name}.provider is unsupported: {provider_value}")
    if provider_value not in provider_names:
        raise ValueError(f"models.{name}.provider is not configured: {provider_value}")
    provider: ProviderName = provider_value
    return ModelConfig(
        name=name,
        provider=provider,
        model=_required_string(row, "model", location=f"models.{name}"),
        timeout=_optional_integer(row, "timeout", 60, location=f"models.{name}"),
        temperature=_optional_number(
            row,
            "temperature",
            0.7,
            location=f"models.{name}",
        ),
        max_tokens=_optional_integer(
            row,
            "max_tokens",
            8192,
            location=f"models.{name}",
        ),
        context_window_tokens=_optional_integer(
            row,
            "context_tokens",
            131072,
            location=f"models.{name}",
        ),
    )


def _object_table(value: object, *, location: str) -> dict[str, object]:
    """Return one string-keyed TOML table or raise a location-aware error."""
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{location} must be a TOML table")
    return {str(key): item for key, item in value.items()}


def _reject_unknown_keys(
    row: dict[str, object],
    allowed: set[str],
    *,
    location: str,
) -> None:
    """Reject misspelled configuration instead of silently using defaults."""
    unknown = sorted(set(row) - allowed)
    if unknown:
        raise ValueError(f"{location} contains unknown field: {unknown[0]}")


def _required_string(row: dict[str, object], key: str, *, location: str) -> str:
    """Read one required non-blank TOML string."""
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_string(
    row: dict[str, object],
    key: str,
    *,
    location: str,
) -> str | None:
    """Read one optional non-blank TOML string without inventing a value."""
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _optional_integer(
    row: dict[str, object],
    key: str,
    default: int,
    *,
    location: str,
) -> int:
    """Read one optional positive TOML integer."""
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{location}.{key} must be a positive integer")
    return value


def _optional_number(
    row: dict[str, object],
    key: str,
    default: float,
    *,
    location: str,
) -> float:
    """Read one optional numeric TOML value."""
    value = row.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # Callers receive one consistent startup-configuration error type for
        # malformed TOML values, including values whose Python type is wrong.
        raise ValueError(f"{location}.{key} must be a number")  # noqa: TRY004
    return float(value)


def _int_value(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _optional_int_value(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_value(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    return float(value)


def _bool_value(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
