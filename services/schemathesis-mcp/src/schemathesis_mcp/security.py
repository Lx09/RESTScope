from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class TargetNotAllowed(ValueError):
    pass


class PathNotAllowed(ValueError):
    pass


@dataclass(frozen=True)
class PathPolicy:
    allowed_roots: tuple[Path, ...]

    def __init__(self, allowed_roots: list[Path] | tuple[Path, ...]) -> None:
        object.__setattr__(self, "allowed_roots", tuple(Path(root).resolve() for root in allowed_roots))

    @classmethod
    def from_env(cls) -> PathPolicy:
        roots = [Path.cwd()]
        value = os.getenv("SCHEMATHESIS_MCP_ALLOWED_PATHS")
        if value:
            roots.extend(Path(item) for item in value.split(os.pathsep) if item)
        return cls(roots)

    def validate(self, target: str) -> Path:
        path = Path(target).expanduser().resolve()
        if not path.is_file():
            raise PathNotAllowed(f"Schema path is not a readable file: {path}")
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise PathNotAllowed(f"Schema path is outside allowed roots: {path}")
        return path


@dataclass(frozen=True)
class TargetPolicy:
    allowed_hosts: set[str] | None = None

    @classmethod
    def from_env(cls) -> TargetPolicy:
        value = os.getenv("SCHEMATHESIS_MCP_ALLOWED_HOSTS")
        if not value:
            return cls()
        return cls({item.strip().lower() for item in value.split(",") if item.strip()})

    def validate(self, target: str) -> None:
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"}:
            return
        hostname = (parsed.hostname or "").lower()
        if self.allowed_hosts is not None and hostname not in self.allowed_hosts:
            raise TargetNotAllowed(f"Target host is not allowed: {hostname}")


class Sanitizer:
    REDACTED = "[REDACTED]"
    sensitive_names = frozenset(
        {
            "authorization",
            "proxy-authorization",
            "x-api-key",
            "api-key",
            "apikey",
            "token",
            "access_token",
            "refresh_token",
            "password",
            "passwd",
            "secret",
            "client_secret",
            "cookie",
            "set-cookie",
        }
    )

    def sanitize(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and key.lower() in self.sensitive_names:
            return self.REDACTED
        if isinstance(value, Mapping):
            return {str(item_key): self.sanitize(item, key=str(item_key)) for item_key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self.sanitize(item) for item in value]
        if isinstance(value, str) and key is not None and key.lower() in {"url", "uri", "location"}:
            return self.sanitize_url(value)
        return value

    def sanitize_url(self, value: str) -> str:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        hostname = parts.hostname or ""
        port = f":{parts.port}" if parts.port is not None else ""
        userinfo = f"{self.REDACTED}@" if parts.username is not None or parts.password is not None else ""
        query = urlencode(
            [
                (name, self.REDACTED if name.lower() in self.sensitive_names else item)
                for name, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, f"{userinfo}{hostname}{port}", parts.path, query, parts.fragment))
