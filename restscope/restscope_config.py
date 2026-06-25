"""Small runtime configuration for the parser-only RESTScope package."""

from __future__ import annotations

from dataclasses import dataclass
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
    """Configuration for one LLM endpoint."""

    provider: str = "openai_compatible"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: int = 60
    temperature: float = 0.7
    max_tokens: int = 128000


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
class RESTScopeConfig:
    """RESTScope configuration loaded from `.env` and environment variables."""

    paths: PathsConfig
    logging: LoggingConfig
    llm: LLMConfig
    db: DBConfig

    @classmethod
    def from_environment(cls, env_file: Path | None = None) -> "RESTScopeConfig":
        values = _merged_environment(env_file or PROJECT_ROOT / ".env")
        data_dir = Path(values.get("DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser()
        log_file = values.get("LOG_FILE")
        thinking = _load_model_config(values, "THINK")

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
                fast=_load_model_config(values, "FAST", fallback=thinking),
            ),
            db=DBConfig(
                url=values.get("DB_URL", "sqlite:///./data/restscope.db"),
                echo=_bool_value(values.get("DB_ECHO"), False),
                pool_size=_optional_int_value(values.get("DB_POOL_SIZE")),
                max_overflow=_optional_int_value(values.get("DB_MAX_OVERFLOW")),
            ),
        )

    @property
    def log_file(self) -> Path:
        return self.logging.file or self.paths.logs_dir_resolved / "restscope.log"


def _load_model_config(
    values: dict[str, str],
    prefix: str,
    *,
    fallback: ModelConfig | None = None,
) -> ModelConfig:
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
        max_tokens=_int_value(
            values.get(f"{prefix}_MAX_TOKENS"),
            fallback.max_tokens if fallback else 128000,
        ),
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
