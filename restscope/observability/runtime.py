"""Tracing facade that keeps Phoenix dependencies optional."""

from __future__ import annotations

import logging

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from restscope.observability.content import PreparedContent, TraceContentEncoder
from restscope.observability.openinference import prepare_message_attributes
from restscope.redaction import Redactor


LOGGER = logging.getLogger(__name__)


class TraceSpan:
    """Small span handle used by business code without importing OpenTelemetry."""

    def __init__(
        self,
        *,
        span: Any | None = None,
        content_encoder: TraceContentEncoder | None = None,
    ) -> None:
        self._span = span
        self._content_encoder = content_encoder
        self._has_error = False

    def set_output(self, value: Any) -> None:
        self._set_content("output", value)

    def set_llm_input_messages(self, messages: Any) -> None:
        """Record model-visible input as Phoenix-renderable chat messages."""

        try:
            normalized = self._normalize_messages(messages)
        except Exception:
            return
        self._set_llm_messages(
            "input",
            normalized,
            summary={
                "message_count": len(normalized),
                "roles": [message.get("role") for message in normalized],
            },
        )

    def set_llm_output_messages(
        self,
        messages: Any,
        *,
        summary: Any | None = None,
    ) -> None:
        """Record normalized model output as Phoenix-renderable chat messages."""

        try:
            normalized = self._normalize_messages(messages)
        except Exception:
            return
        self._set_llm_messages(
            "output",
            normalized,
            summary=summary
            if summary is not None
            else {
                "message_count": len(normalized),
                "roles": [message.get("role") for message in normalized],
            },
        )

    def set_attribute(self, name: str, value: Any) -> None:
        if self._span is None or self._content_encoder is None:
            return
        try:
            redacted = self._content_encoder.redactor.redact(value)
            if isinstance(redacted, dict | list):
                redacted = self._content_encoder.prepare(redacted).value
            self._span.set_attribute(name, redacted)
        except Exception:
            return

    def set_input(self, value: Any) -> None:
        self._set_content("input", value)

    def record_error(self, exc: BaseException) -> None:
        if self._span is None or self._content_encoder is None:
            return
        try:
            message = self._content_encoder.redactor.redact_text(str(exc))
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
        if self._span is None or self._content_encoder is None:
            return
        try:
            from opentelemetry.trace.status import Status, StatusCode

            redacted = self._content_encoder.redactor.redact_text(message)
            self._span.set_status(Status(StatusCode.ERROR, redacted))
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
        if self._span is None or self._content_encoder is None:
            return
        try:
            prepared = self._content_encoder.prepare(value)
            self._span.set_attribute(f"{prefix}.value", prepared.value)
            self._span.set_attribute(f"{prefix}.mime_type", "application/json")
            self._set_size_attributes(prefix, prepared)
        except Exception:
            return

    def _set_llm_messages(
        self,
        prefix: str,
        messages: list[dict[str, Any]],
        *,
        summary: Any,
    ) -> None:
        if self._span is None or self._content_encoder is None:
            return
        try:
            normalized_summary = self._content_encoder.redactor.redact(summary)
            prepared_summary = self._content_encoder.prepare(normalized_summary)
            summary_size = len(prepared_summary.value.encode("utf-8"))
            message_budget = max(
                0,
                self._content_encoder.max_content_bytes - summary_size,
            )
            prepared_messages = prepare_message_attributes(
                messages,
                direction=prefix,
                max_value_bytes=message_budget,
            )
            self._span.set_attribute(f"{prefix}.value", prepared_summary.value)
            self._span.set_attribute(f"{prefix}.mime_type", "application/json")
            for name, value in prepared_messages.attributes.items():
                self._span.set_attribute(name, value)
            if prepared_messages.omitted_message_count:
                self._span.set_attribute(
                    f"restscope.{prefix}.omitted_message_count",
                    prepared_messages.omitted_message_count,
                )
            original_size = len(
                _compact_json_bytes(
                    {
                        "summary": normalized_summary,
                        "messages": messages,
                    }
                )
            )
            self._set_size_attributes(
                prefix,
                PreparedContent(
                    value=prepared_summary.value,
                    original_size_bytes=original_size,
                    truncated=(
                        prepared_summary.truncated
                        or prepared_messages.truncated
                    ),
                ),
            )
        except Exception:
            return

    def _normalize_messages(self, messages: Any) -> list[dict[str, Any]]:
        if self._content_encoder is None:
            return []
        normalized = self._content_encoder.redactor.redact(messages)
        if not isinstance(normalized, list):
            return []
        return [message for message in normalized if isinstance(message, dict)]

    def _set_size_attributes(self, prefix: str, prepared: PreparedContent) -> None:
        self._span.set_attribute(
            f"restscope.{prefix}.original_size_bytes",
            prepared.original_size_bytes,
        )
        self._span.set_attribute(
            f"restscope.{prefix}.truncated",
            prepared.truncated,
        )


def _compact_json_bytes(value: Any) -> bytes:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class TracingRuntime:
    """App-owned tracing facade; disabled instances are dependency-free no-ops."""

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        max_content_bytes: int = 65536,
        backend: Any | None = None,
    ) -> None:
        self._redactor = redactor or Redactor()
        self._content_encoder = TraceContentEncoder(
            redactor=self._redactor,
            max_content_bytes=max_content_bytes,
        )
        self._backend = backend
        self._closed = False

    @classmethod
    def disabled(cls, *, redactor: Redactor | None = None) -> "TracingRuntime":
        return cls(redactor=redactor)

    @property
    def enabled(self) -> bool:
        return self._backend is not None and not self._closed

    @property
    def redactor(self) -> Redactor:
        return self._redactor

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

        span = TraceSpan(span=otel_span, content_encoder=self._content_encoder)
        span.set_attribute("openinference.span.kind", kind)
        if kind == "AGENT":
            span.set_attribute("agent.name", name)
        elif kind == "TOOL":
            span.set_attribute("tool.name", name)
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
            self.redactor.redact_text(str(exc)),
        )


def build_tracing_runtime(
    config: Any,
    *,
    redactor: Redactor | None = None,
) -> TracingRuntime:
    """Build Phoenix tracing when enabled, otherwise return a safe no-op."""

    shared_redactor = redactor or Redactor()
    shared_redactor.register_secrets((getattr(config, "api_key", ""),))
    max_content_bytes = getattr(config, "max_content_bytes", 65536)
    if not getattr(config, "enabled", False):
        return TracingRuntime(
            redactor=shared_redactor,
            max_content_bytes=max_content_bytes,
        )
    try:
        from restscope.observability.phoenix import build_phoenix_backend

        backend = build_phoenix_backend(config=config)
    except Exception as exc:  # Missing extras and initialization failures are fail-open.
        LOGGER.warning(
            "Tracing initialization failed; continuing without tracing: %s",
            shared_redactor.redact_text(str(exc)),
        )
        backend = None
    return TracingRuntime(
        redactor=shared_redactor,
        max_content_bytes=max_content_bytes,
        backend=backend,
    )
