"""Central exact-value redaction for RESTScope outputs and traces."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from threading import RLock


class Redactor:
    """Replace only explicitly registered secret values."""

    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        self._lock = RLock()
        self._secrets = {value for value in secret_values if value}

    def register_secrets(self, values: Iterable[str]) -> None:
        """Add exact secret values without exposing them through object state."""

        with self._lock:
            self._secrets.update(value for value in values if value)

    def redact(self, value: object) -> object:
        """Recursively convert a value to secret-safe, JSON-friendly data."""

        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif is_dataclass(value) and not isinstance(value, type):
            value = asdict(value)

        if isinstance(value, Mapping):
            redacted: dict[str, object] = {}
            for key, item in value.items():
                base_key = self.redact_text(str(key))
                redacted_key = base_key
                suffix = 2
                while redacted_key in redacted:
                    redacted_key = f"{base_key}#{suffix}"
                    suffix += 1
                redacted[redacted_key] = self.redact(item)
            return redacted
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [self.redact(item) for item in value]
        if isinstance(value, bytes | bytearray):
            return self.redact_text(bytes(value).decode("utf-8", errors="replace"))
        if isinstance(value, str):
            return self.redact_text(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        return self.redact_text(str(value))

    def redact_text(self, value: str) -> str:
        """Replace every registered value wherever it occurs in text."""

        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        result = value
        for secret in secrets:
            result = result.replace(secret, "***REDACTED***")
        return result

    def __repr__(self) -> str:
        with self._lock:
            secret_count = len(self._secrets)
        return f"Redactor(secret_count={secret_count})"
