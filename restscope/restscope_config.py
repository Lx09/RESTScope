"""Small runtime configuration for the parser-only RESTScope package."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


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
        """
        Handle logs dir resolved as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        logs_dir = self.data_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir


@dataclass(frozen=True)
class LoggingConfig:
    """Logging settings controlled by short environment names."""

    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Path | None = None
    third_party_level: str = "WARNING"


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for one LLM endpoint.

    ``context_window_tokens`` is the model's total request capacity, while
    ``max_tokens`` is reserved for one response. Keeping them separate lets
    prompt builders use the remaining space for evidence without relying on a
    provider-specific model registry.
    """

    provider: str = "openai_compatible"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: int = 60
    temperature: float = 0.7
    max_tokens: int = 8192
    context_window_tokens: int = 131072
    reasoning_mode: str = "default"
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        """Reject a response reservation that leaves no prompt capacity."""
        if self.max_tokens >= self.context_window_tokens:
            raise ValueError(
                "max_tokens must be smaller than context_window_tokens"
            )


@dataclass(frozen=True)
class LLMConfig:
    """Strong and fast model configuration."""

    thinking: ModelConfig
    fast: ModelConfig


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
            raise ValueError("RANDOM_SEED must be non-negative")


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
class RESTScopeConfig:
    """RESTScope configuration loaded from `.env` and environment variables."""

    paths: PathsConfig
    logging: LoggingConfig
    llm: LLMConfig
    db: DBConfig
    mcp: MCPConfig
    tracing: TracingConfig = field(default_factory=TracingConfig)
    random: RandomConfig = field(default_factory=RandomConfig)

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> "RESTScopeConfig":
        """
        Handle from environment as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        values = _merged_environment(env_file or PROJECT_ROOT / ".env")
        data_dir = Path(values.get("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
        log_file = values.get("LOG_FILE")
        thinking = _load_model_config(
            values,
            "THINK",
            default_reasoning_mode="enabled",
        )

        return cls(
            paths=PathsConfig(data_dir=data_dir),
            logging=LoggingConfig(
                level=values.get("LOG_LEVEL", "INFO"),
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                file=Path(log_file).expanduser() if log_file else None,
                third_party_level=values.get("THIRD_PARTY_LOG_LEVEL", "WARNING"),
            ),
            llm=LLMConfig(
                thinking=thinking,
                fast=_load_model_config(
                    values,
                    "FAST",
                    fallback=thinking,
                    default_reasoning_mode="disabled",
                ),
            ),
            db=DBConfig(
                url=values.get("DB_URL", "sqlite:///./data/restscope.db"),
                echo=_bool_value(values.get("DB_ECHO"), False),
                pool_size=_optional_int_value(values.get("DB_POOL_SIZE")),
                max_overflow=_optional_int_value(values.get("DB_MAX_OVERFLOW")),
            ),
            mcp=MCPConfig(
                servers_file=Path(values.get("MCP_SERVERS_FILE", str(PROJECT_ROOT / "mcp.servers.json"))).expanduser(),
            ),
            tracing=TracingConfig(
                enabled=_bool_value(values.get("TRACING_ENABLED"), False),
                collector_endpoint=values.get(
                    "PHOENIX_COLLECTOR_ENDPOINT",
                    "http://localhost:6006",
                ),
                project_name=values.get("PHOENIX_PROJECT_NAME", "restscope"),
                api_key=values.get("PHOENIX_API_KEY", ""),
                protocol=values.get("PHOENIX_PROTOCOL", "http/protobuf"),
                batch=_bool_value(values.get("TRACING_BATCH"), True),
                max_content_bytes=_int_value(
                    values.get("TRACING_MAX_CONTENT_BYTES"),
                    65536,
                ),
                flush_timeout_seconds=_float_value(
                    values.get("TRACING_FLUSH_TIMEOUT_SECONDS"),
                    5.0,
                ),
            ),
            random=RandomConfig(
                seed=_optional_int_value(values.get("RANDOM_SEED")),
            ),
        )

    @property
    def log_file(self) -> Path:
        """
        Handle log file as part of the RESTScope application runtime.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self.logging.file or self.paths.logs_dir_resolved / "restscope.log"


def _load_model_config(
    values: dict[str, str],
    prefix: str,
    *,
    fallback: ModelConfig | None = None,
    default_reasoning_mode: str = "default",
) -> ModelConfig:
    max_tokens = _int_value(
        values.get(f"{prefix}_MAX_TOKENS"),
        8192,
    )
    context_window_tokens = _int_value(
        values.get(f"{prefix}_CONTEXT_WINDOW_TOKENS"),
        131072,
    )
    if max_tokens >= context_window_tokens:
        raise ValueError(
            f"{prefix}_MAX_TOKENS must be smaller than "
            f"{prefix}_CONTEXT_WINDOW_TOKENS"
        )
    return ModelConfig(
        provider=values.get(f"{prefix}_PROVIDER", fallback.provider if fallback else "openai_compatible"),
        model=values.get(f"{prefix}_MODEL", fallback.model if fallback else ""),
        api_key=values.get(f"{prefix}_API_KEY", fallback.api_key if fallback else ""),
        base_url=values.get(f"{prefix}_BASE_URL", fallback.base_url if fallback else ""),
        timeout=_int_value(values.get(f"{prefix}_TIMEOUT"), fallback.timeout if fallback else 60),
        temperature=_float_value(
            values.get(f"{prefix}_TEMPERATURE"),
            fallback.temperature if fallback else 0.7,
        ),
        max_tokens=max_tokens,
        context_window_tokens=context_window_tokens,
        reasoning_mode=values.get(f"{prefix}_REASONING_MODE", default_reasoning_mode),
        reasoning_effort=values.get(f"{prefix}_REASONING_EFFORT") or None,
    )


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


CONFIG = RESTScopeConfig.from_environment()
