"""Tracing facade that keeps Phoenix dependencies optional."""

from __future__ import annotations

import logging

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from restscope.observability.sanitizer import PreparedContent, TraceSanitizer


LOGGER = logging.getLogger(__name__)


class TraceSpan:
    """Small span handle used by business code without importing OpenTelemetry."""

    def __init__(self, *, span: Any | None = None, sanitizer: TraceSanitizer | None = None) -> None:
        self._span = span
        self._sanitizer = sanitizer
        self._has_error = False

    def set_output(self, value: Any) -> None:
        self._set_content("output", value)

    def set_attribute(self, name: str, value: Any) -> None:
        if self._span is None or self._sanitizer is None:
            return
        try:
            sanitized = self._sanitizer.sanitize(value)
            if isinstance(sanitized, dict | list):
                sanitized = self._sanitizer.prepare(sanitized).value
            self._span.set_attribute(name, sanitized)
        except Exception:
            return

    def set_input(self, value: Any) -> None:
        self._set_content("input", value)

    def record_error(self, exc: BaseException) -> None:
        if self._span is None or self._sanitizer is None:
            return
        try:
            message = self._sanitizer.sanitize_text(str(exc))
            self.mark_error(message)
            self._span.add_event(
                "exception",
                {
                    "exception.type": type(exc).__name__,
                    "exception.message": message,
                },
            )
        except Exception:
            return

    def mark_error(self, message: str) -> None:
        if self._span is None or self._sanitizer is None:
            return
        try:
            from opentelemetry.trace.status import Status, StatusCode

            sanitized = self._sanitizer.sanitize_text(message)
            self._span.set_status(Status(StatusCode.ERROR, sanitized))
            self._has_error = True
        except Exception:
            return

    def mark_ok(self) -> None:
        if self._span is None or self._has_error:
            return
        try:
            from opentelemetry.trace.status import Status, StatusCode

            self._span.set_status(Status(StatusCode.OK))
        except Exception:
            return

    def _set_content(self, prefix: str, value: Any) -> None:
        if self._span is None or self._sanitizer is None:
            return
        try:
            prepared = self._sanitizer.prepare(value)
            self._span.set_attribute(f"{prefix}.value", prepared.value)
            self._span.set_attribute(f"{prefix}.mime_type", "application/json")
            self._set_size_attributes(prefix, prepared)
        except Exception:
            return

    def _set_size_attributes(self, prefix: str, prepared: PreparedContent) -> None:
        self._span.set_attribute(
            f"restscope.{prefix}.original_size_bytes",
            prepared.original_size_bytes,
        )
        self._span.set_attribute(
            f"restscope.{prefix}.truncated",
            prepared.truncated,
        )


class TracingRuntime:
    """App-owned tracing facade; disabled instances are dependency-free no-ops."""

    def __init__(
        self,
        *,
        sanitizer: TraceSanitizer,
        backend: Any | None = None,
    ) -> None:
        self._sanitizer = sanitizer
        self._backend = backend
        self._closed = False

    @classmethod
    def disabled(cls) -> "TracingRuntime":
        return cls(sanitizer=TraceSanitizer())

    @property
    def enabled(self) -> bool:
        return self._backend is not None and not self._closed

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str,
        input_value: Any | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[TraceSpan]:
        if not self.enabled:
            yield TraceSpan()
            return
        try:
            manager = self._backend.start_as_current_span(name)
            otel_span = manager.__enter__()
        except Exception as exc:
            self._warn("Tracing span creation failed", exc)
            yield TraceSpan()
            return

        span = TraceSpan(span=otel_span, sanitizer=self._sanitizer)
        span.set_attribute("openinference.span.kind", kind)
        if input_value is not None:
            span.set_input(input_value)
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)

        try:
            yield span
        except BaseException as exc:
            span.record_error(exc)
            raise
        else:
            span.mark_ok()
        finally:
            try:
                manager.__exit__(None, None, None)
            except Exception as exc:
                self._warn("Tracing span finalization failed", exc)

    def register_secrets(self, values: Iterable[str]) -> None:
        self._sanitizer.register_secrets(values)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._backend is None:
            return
        try:
            self._backend.close()
        except Exception as exc:  # pragma: no cover - backend-specific failure.
            self._warn("Tracing shutdown failed", exc)

    def __repr__(self) -> str:
        state = "enabled" if self.enabled else "disabled"
        return f"TracingRuntime(state={state!r})"

    def _warn(self, message: str, exc: BaseException) -> None:
        LOGGER.warning(
            "%s: %s",
            message,
            self._sanitizer.sanitize_text(str(exc)),
        )


def build_tracing_runtime(
    config: Any,
    *,
    secret_values: Iterable[str] = (),
) -> TracingRuntime:
    """Build Phoenix tracing when enabled, otherwise return a safe no-op."""

    sanitizer = TraceSanitizer(
        secret_values=[*secret_values, getattr(config, "api_key", "")],
        max_content_bytes=getattr(config, "max_content_bytes", 65536),
    )
    if not getattr(config, "enabled", False):
        return TracingRuntime(sanitizer=sanitizer)
    try:
        from restscope.observability.phoenix import build_phoenix_backend

        backend = build_phoenix_backend(config=config, sanitizer=sanitizer)
    except Exception as exc:  # Missing extras and initialization failures are fail-open.
        LOGGER.warning(
            "Tracing initialization failed; continuing without tracing: %s",
            sanitizer.sanitize_text(str(exc)),
        )
        backend = None
    return TracingRuntime(sanitizer=sanitizer, backend=backend)
