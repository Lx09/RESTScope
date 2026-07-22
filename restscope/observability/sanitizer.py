"""Prepare bounded, secret-safe values for trace attributes."""

from __future__ import annotations

import json

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from threading import RLock
from typing import Any


@dataclass(frozen=True)
class PreparedContent:
    """One serialized trace value plus its size metadata."""

    value: str
    original_size_bytes: int
    truncated: bool


@dataclass
class TraceSanitizer:
    """Recursively redact and size-bound values before trace export."""

    secret_values: Iterable[str] = field(default_factory=tuple, repr=False)
    max_content_bytes: int = 65536

    def __post_init__(self) -> None:
        # Import lazily so the LLM client can depend on the tracing facade
        # without creating a package-initialization cycle.
        from restscope.llm.redactor import Redactor

        self._redactor = Redactor()
        self._secret_keys = {
            key.strip().lower().replace("-", "_")
            for key in self._redactor.SECRET_KEYS
        }
        self._lock = RLock()
        self._secrets = {value for value in self.secret_values if value}

    def register_secrets(self, values: Iterable[str]) -> None:
        with self._lock:
            self._secrets.update(value for value in values if value)

    def prepare(self, value: Any) -> PreparedContent:
        serialized = json.dumps(
            self.sanitize(value),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        original_size = len(serialized.encode("utf-8"))
        if original_size <= self.max_content_bytes:
            return PreparedContent(
                value=serialized,
                original_size_bytes=original_size,
                truncated=False,
            )
        return PreparedContent(
            value=self._truncated_value(serialized),
            original_size_bytes=original_size,
            truncated=True,
        )

    def sanitize(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)

        if isinstance(value, Mapping):
            return self._sanitize_mapping(value)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [self.sanitize(item) for item in value]
        if isinstance(value, bytes | bytearray):
            return self._sanitize_text(bytes(value).decode("utf-8", errors="replace"))
        if isinstance(value, str):
            return self._sanitize_text(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._sanitize_text(str(value))

    def sanitize_text(self, value: str) -> str:
        return self._sanitize_text(value)

    def _sanitize_mapping(self, value: Mapping[Any, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = key.strip().lower().replace("-", "_")
            if normalized_key == "reasoning_content":
                text = "" if item is None else str(item)
                sanitized[f"{key}_present"] = bool(text)
                sanitized[f"{key}_length"] = len(text)
            elif normalized_key in self._secret_keys:
                sanitized[key] = "***REDACTED***"
            else:
                sanitized[key] = self.sanitize(item)
        return sanitized

    def _sanitize_text(self, value: str) -> str:
        result = self._redactor.redact_text(value)
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            result = result.replace(secret, "***REDACTED***")
        return result

    def _truncated_value(self, serialized: str) -> str:
        encoded = serialized.encode("utf-8")
        preview_size = min(len(encoded), self.max_content_bytes)
        while preview_size >= 0:
            preview = encoded[:preview_size].decode("utf-8", errors="ignore")
            payload = json.dumps(
                {"preview": preview, "truncated": True},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(payload.encode("utf-8")) <= self.max_content_bytes:
                return payload
            preview_size -= max(1, preview_size // 8)
        return "{}"
