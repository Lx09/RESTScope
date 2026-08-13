"""OpenTelemetry backend primitives used by the optional Phoenix runtime."""

from __future__ import annotations

from threading import Thread


class OpenTelemetryBackend:
    """Own a tracer provider and enforce a bounded best-effort shutdown."""

    def __init__(
        self,
        *,
        tracer_provider: object,
        flush_timeout_seconds: float,
    ) -> None:
        self.tracer_provider = tracer_provider
        self.flush_timeout_seconds = max(0.0, flush_timeout_seconds)
        self.tracer = tracer_provider.get_tracer("restscope")
        self._closed = False

    def start_as_current_span(self, name: str):
        """Open an OpenTelemetry span with the requested name, kind, attributes, and parent context."""
        return self.tracer.start_as_current_span(
            name,
            record_exception=False,
            set_status_on_exception=False,
        )

    def close(self) -> None:
        """Flush and shut down the owned OpenTelemetry provider, if one was created."""
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        def shutdown() -> None:
            try:
                self.tracer_provider.force_flush(
                    timeout_millis=int(self.flush_timeout_seconds * 1000),
                )
                self.tracer_provider.shutdown()
            except BaseException as exc:  # Propagated to the fail-open facade.  # noqa: BLE001
                errors.append(exc)

        worker = Thread(target=shutdown, name="restscope-tracing-shutdown", daemon=True)
        worker.start()
        worker.join(timeout=self.flush_timeout_seconds)
        if worker.is_alive():
            raise TimeoutError(
                f"Tracing shutdown exceeded {self.flush_timeout_seconds:g} seconds"
            )
        if errors:
            raise RuntimeError("Tracing shutdown failed") from errors[0]
