"""Phoenix registration and process-wide tracing backend coordination."""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from urllib.parse import urlparse, urlunparse

from phoenix.otel import register

from restscope.observability.otel_backend import OpenTelemetryBackend


@dataclass
class _SharedBackend:
    key: tuple[Any, ...] = field(repr=False)
    backend: OpenTelemetryBackend
    proxy_environment: "_ProxyEnvironment | None"
    references: int = 1


@dataclass(frozen=True)
class _ProxyEnvironment:
    previous: dict[str, str | None] = field(repr=False)

    def restore(self) -> None:
        """
        Handle restore as part of bounded, redacted tracing and telemetry.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class _BackendLease:
    """One App's reference to the shared Phoenix tracing backend."""

    def __init__(self, shared: _SharedBackend) -> None:
        self._shared = shared
        self._closed = False

    def start_as_current_span(self, name: str):
        """
        Handle start as current span as part of bounded, redacted tracing and telemetry.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._shared.backend.start_as_current_span(name)

    def close(self) -> None:
        """
        Release resources owned by bounded, redacted tracing and telemetry.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        global _ACTIVE_BACKEND

        if self._closed:
            return
        self._closed = True
        with _BACKEND_LOCK:
            self._shared.references -= 1
            if self._shared.references == 0:
                if _ACTIVE_BACKEND is self._shared:
                    _ACTIVE_BACKEND = None
                try:
                    self._shared.backend.close()
                finally:
                    if self._shared.proxy_environment is not None:
                        self._shared.proxy_environment.restore()


_BACKEND_LOCK = RLock()
_ACTIVE_BACKEND: _SharedBackend | None = None


def build_phoenix_backend(*, config: Any) -> _BackendLease:
    """Register or reuse the process-wide Phoenix tracing backend."""

    global _ACTIVE_BACKEND

    key = (
        config.collector_endpoint,
        config.project_name,
        config.api_key,
        config.protocol,
        config.batch,
        config.flush_timeout_seconds,
    )
    with _BACKEND_LOCK:
        if _ACTIVE_BACKEND is not None:
            if _ACTIVE_BACKEND.key != key:
                raise RuntimeError(
                    "A Phoenix tracing runtime with different configuration is already active"
                )
            _ACTIVE_BACKEND.references += 1
            return _BackendLease(_ACTIVE_BACKEND)

        proxy_environment = _configure_loopback_no_proxy(config.collector_endpoint)
        try:
            tracer_provider = register(
                endpoint=_collector_export_endpoint(
                    config.collector_endpoint,
                    protocol=config.protocol,
                ),
                project_name=config.project_name,
                api_key=config.api_key or None,
                protocol=config.protocol,
                batch=config.batch,
                set_global_tracer_provider=False,
                auto_instrument=False,
                verbose=False,
            )
            backend = OpenTelemetryBackend(
                tracer_provider=tracer_provider,
                flush_timeout_seconds=config.flush_timeout_seconds,
            )
        except Exception:
            try:
                if "backend" in locals():
                    backend.close()
            finally:
                if proxy_environment is not None:
                    proxy_environment.restore()
            raise
        _ACTIVE_BACKEND = _SharedBackend(
            key=key,
            backend=backend,
            proxy_environment=proxy_environment,
        )
        return _BackendLease(_ACTIVE_BACKEND)


def _configure_loopback_no_proxy(endpoint: str) -> _ProxyEnvironment | None:
    hostname = (urlparse(endpoint).hostname or "").lower()
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None

    previous = {name: os.environ.get(name) for name in ("NO_PROXY", "no_proxy")}
    required = {"localhost", "127.0.0.1", "::1", hostname}
    for name, original in previous.items():
        existing = [item.strip() for item in (original or "").split(",") if item.strip()]
        combined = [*existing, *sorted(required.difference(existing))]
        os.environ[name] = ",".join(combined)
    return _ProxyEnvironment(previous=previous)


def _collector_export_endpoint(endpoint: str, *, protocol: str) -> str:
    if protocol != "http/protobuf":
        return endpoint
    parsed = urlparse(endpoint)
    if parsed.path not in {"", "/"}:
        return endpoint
    return urlunparse(parsed._replace(path="/v1/traces"))
