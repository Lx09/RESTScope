"""Tracing facade that keeps Phoenix dependencies optional."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from restscope.observability.content import PreparedContent, TraceContentEncoder
from restscope.observability.openinference import prepare_message_attributes

from .redaction import Redactor

LOGGER = logging.getLogger(__name__)


class TraceSpan:
    """Small span handle used by business code without importing OpenTelemetry."""

    def __init__(
        self,
        *,
        span: object | None = None,
        content_encoder: TraceContentEncoder | None = None,
        live_span: object | None = None,
    ) -> None:
        self._span = span
        self._content_encoder = content_encoder
        self._live_span = live_span
        self._has_error = False

    def set_output(self, value: object) -> None:
        """Attach a bounded, redacted output value to both trace and live-observer spans."""
        self._set_content("output", value)

    def set_llm_input_messages(self, messages: object) -> None:
        """Record model-visible input as Phoenix-renderable chat messages."""

        try:
            normalized = self._normalize_messages(messages)
        except Exception:  # noqa: BLE001
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
        messages: object,
        *,
        summary: object | None = None,
    ) -> None:
        """Record normalized model output as Phoenix-renderable chat messages."""

        try:
            normalized = self._normalize_messages(messages)
        except Exception:  # noqa: BLE001
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

    def set_attribute(self, name: str, value: object) -> None:
        """Attach one safe scalar attribute without allowing telemetry failure to affect the tested workflow."""
        if self._live_span is not None:
            try:
                self._live_span.set_attribute(name, value)
            except Exception:  # noqa: BLE001, S110
                pass
        if self._span is None or self._content_encoder is None:
            return
        try:
            redacted = self._content_encoder.redactor.redact(value)
            if isinstance(redacted, dict | list):
                redacted = self._content_encoder.prepare(redacted).value
            self._span.set_attribute(name, redacted)
        except Exception:  # noqa: BLE001
            return

    def set_input(self, value: object) -> None:
        """Attach a bounded, redacted input value to both trace and live-observer spans."""
        self._set_content("input", value)

    def set_live_detail(self, name: str, value: object) -> None:
        """Add one observer-only detail without changing Phoenix attributes.

        Args:
            name: Stable field name inside the semantic event detail.
            value: JSON-compatible evidence owned by the current workflow.

        This narrow outlet is used when the semantic UI needs a value, such as
        a generated Batch seed, that was deliberately absent from the
        established exported span contract. Observer failure remains fail-open.
        """
        if self._live_span is None:
            return
        try:
            self._live_span.set_detail(name, value)
        except Exception:  # noqa: BLE001, S110
            pass

    def record_error(self, exc: BaseException) -> None:
        """Record a redacted internal traceback without exposing it to callers."""
        if self._live_span is not None:
            try:
                if isinstance(exc, KeyboardInterrupt):
                    # A caller stop is terminal for the local Run but is not a
                    # failed Agent decision, tool, or Batch in the read-only UI.
                    self._live_span.mark_interrupted()
                else:
                    self._live_span.mark_error(str(exc))
            except Exception:  # noqa: BLE001, S110
                pass
        if self._span is None or self._content_encoder is None:
            return
        try:
            message = self._content_encoder.redactor.redact_text(str(exc))
            stacktrace = self._content_encoder.redactor.redact_text(
                "".join(traceback.format_exception(exc))
            )
            # The live observer was updated above, including the special
            # stopped-warning treatment for KeyboardInterrupt. Set only the
            # exported span here so Phoenix cannot overwrite that UI status.
            self._mark_export_error(message)
            self._span.add_event(
                "exception",
                {
                    "exception.type": type(exc).__name__,
                    "exception.message": message,
                    "exception.stacktrace": stacktrace,
                },
            )
        except Exception:  # noqa: BLE001
            return

    def mark_error(self, message: str) -> None:
        """Mark the span failed and record the bounded exception description."""
        if self._live_span is not None:
            try:
                self._live_span.mark_error(message)
            except Exception:  # noqa: BLE001, S110
                pass
        if self._span is None or self._content_encoder is None:
            return
        try:
            redacted = self._content_encoder.redactor.redact_text(message)
            self._mark_export_error(redacted)
        except Exception:  # noqa: BLE001
            return

    def mark_ok(self) -> None:
        """Mark the span successful unless an earlier error already owns its terminal status."""
        if self._live_span is not None and not self._has_error:
            try:
                self._live_span.mark_ok()
            except Exception:  # noqa: BLE001, S110
                pass
        if self._span is None or self._has_error:
            return
        try:
            from opentelemetry.trace.status import Status, StatusCode

            self._span.set_status(Status(StatusCode.OK))
        except Exception:  # noqa: BLE001
            return

    def _set_content(self, prefix: str, value: object) -> None:
        if self._live_span is not None:
            try:
                self._live_span.set_content(prefix, value)
            except Exception:  # noqa: BLE001, S110
                pass
        if self._span is None or self._content_encoder is None:
            return
        try:
            prepared = self._content_encoder.prepare(value)
            self._span.set_attribute(f"{prefix}.value", prepared.value)
            self._span.set_attribute(f"{prefix}.mime_type", "application/json")
            self._set_size_attributes(prefix, prepared)
        except Exception:  # noqa: BLE001
            return

    def _mark_export_error(self, message: str) -> None:
        """Set only Phoenix/OpenTelemetry failure state, never live UI state."""
        if self._span is None:
            return
        from opentelemetry.trace.status import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR, message))
        self._has_error = True

    def _set_llm_messages(
        self,
        prefix: str,
        messages: list[dict[str, object]],
        *,
        summary: object,
    ) -> None:
        """Encode provider-independent chat messages into OpenInference span attributes."""
        if self._live_span is not None:
            try:
                self._live_span.set_messages(prefix, messages, summary=summary)
            except Exception:  # noqa: BLE001, S110
                pass
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
        except Exception:  # noqa: BLE001
            return

    def _normalize_messages(self, messages: object) -> list[dict[str, object]]:
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


def _compact_json_bytes(value: object) -> bytes:
    import json

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class TracingRuntime:
    """App-owned tracing facade; disabled instances are dependency-free no-ops.

    Business code always uses this facade, never OpenTelemetry directly. Values
    are redacted and size-bounded before export, and exporter failures are
    swallowed with warnings so tracing cannot change API testing behavior.
    """

    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        max_content_bytes: int = 65536,
        backend: object | None = None,
        run_observer: object | None = None,
    ) -> None:
        self._redactor = redactor or Redactor()
        self._content_encoder = TraceContentEncoder(
            redactor=self._redactor,
            max_content_bytes=max_content_bytes,
        )
        self._backend = backend
        self._run_observer = run_observer
        self._closed = False

    @classmethod
    def disabled(cls, *, redactor: Redactor | None = None) -> TracingRuntime:
        """Create a no-export runtime that preserves the same context-manager API."""
        return cls(redactor=redactor)

    @property
    def enabled(self) -> bool:
        """Return true only while a backend exists and has not been closed."""
        return self._backend is not None and not self._closed

    @property
    def redactor(self) -> Redactor:
        """Expose the same redactor used for every trace input and output."""
        return self._redactor

    @property
    def run_observer(self) -> object | None:
        """Return the optional current-run observer used by UI adapters."""
        return self._run_observer

    @property
    def live_enabled(self) -> bool:
        """Report whether a live observer can currently accept run events."""
        return self._run_observer is not None and not self._closed

    def bind_run_observer(self, observer: object | None) -> None:
        """Attach one App-owned observer without changing Phoenix registration."""
        self._run_observer = observer

    @contextmanager
    def live_span(
        self,
        name: str,
        *,
        kind: str,
        input_value: object | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[TraceSpan]:
        """Open a UI-only aggregation scope that never changes Phoenix.

        This narrow outlet covers observer evidence that was not represented by
        an existing exported span, such as direct Resolution tool execution.
        It mirrors :meth:`span` fail-open behavior, records business exceptions,
        and always re-raises the original exception unchanged.
        """
        live_span = None
        if self.live_enabled:
            try:
                live_span = self._run_observer.start_span(
                    name=name,
                    kind=kind,
                    input_value=input_value,
                    attributes=attributes,
                )
            except Exception:  # noqa: BLE001
                live_span = None
        span = TraceSpan(
            content_encoder=self._content_encoder,
            live_span=live_span,
        )
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
            if live_span is not None:
                try:
                    live_span.finish()
                except Exception:  # noqa: BLE001, S110
                    pass

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str,
        input_value: object | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> Iterator[TraceSpan]:
        """Open a best-effort span and always yield a safe local handle.

        Creation/finalization failures degrade to a no-op span. Exceptions from
        business code are recorded and re-raised unchanged.
        """
        if not self.enabled and not self.live_enabled:
            yield TraceSpan()
            return
        live_span = None
        if self._run_observer is not None:
            try:
                live_span = self._run_observer.start_span(
                    name=name,
                    kind=kind,
                    input_value=input_value,
                    attributes=attributes,
                )
            except Exception:  # noqa: BLE001
                live_span = None
        manager = None
        otel_span = None
        if self.enabled:
            try:
                manager = self._backend.start_as_current_span(name)
                otel_span = manager.__enter__()
            except Exception as exc:  # noqa: BLE001
                self._warn("Tracing span creation failed", exc)

        span = TraceSpan(
            span=otel_span,
            content_encoder=self._content_encoder,
            live_span=live_span,
        )
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
            if manager is not None:
                try:
                    manager.__exit__(None, None, None)
                except Exception as exc:  # noqa: BLE001
                    self._warn("Tracing span finalization failed", exc)
            if live_span is not None:
                try:
                    live_span.finish()
                except Exception:  # noqa: BLE001, S110
                    pass

    def close(self) -> None:
        """Flush tracing once, close the live observer binding, and make later close calls harmless."""
        if self._closed:
            return
        self._closed = True
        if self._backend is None:
            return
        try:
            self._backend.close()
        except Exception as exc:  # pragma: no cover - backend-specific failure.  # noqa: BLE001
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
    config: object,
    *,
    redactor: Redactor | None = None,
    run_observer: object | None = None,
) -> TracingRuntime:
    """Build Phoenix tracing when enabled, otherwise return a safe no-op."""

    shared_redactor = redactor or Redactor()
    shared_redactor.register_secrets((getattr(config, "api_key", ""),))
    max_content_bytes = getattr(config, "max_content_bytes", 65536)
    if not getattr(config, "enabled", False):
        return TracingRuntime(
            redactor=shared_redactor,
            max_content_bytes=max_content_bytes,
            run_observer=run_observer,
        )
    try:
        from restscope.observability.phoenix import build_phoenix_backend

        backend = build_phoenix_backend(config=config)
    except Exception as exc:  # Missing extras and initialization failures are fail-open.  # noqa: BLE001
        LOGGER.warning(
            "Tracing initialization failed; continuing without tracing: %s",
            shared_redactor.redact_text(str(exc)),
        )
        backend = None
    return TracingRuntime(
        redactor=shared_redactor,
        max_content_bytes=max_content_bytes,
        backend=backend,
        run_observer=run_observer,
    )
