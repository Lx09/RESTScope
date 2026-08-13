"""Attach one bounded target HTTP exchange to its live HTTP Tool event."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import TYPE_CHECKING

from .projection import response_detail as _response_detail

if TYPE_CHECKING:
    from .observer import LiveRunObserver


class LiveHTTPExchange:
    """Complete one HTTP Tool card with bounded request/response evidence."""

    def __init__(
        self,
        *,
        observer: LiveRunObserver,
        event_id: str,
    ) -> None:
        """Remember the semantic owner for the in-flight HTTP request."""

        self._observer = observer
        self._event_id = event_id
        self._started = time.monotonic()
        self._closed = False

    def finish(self, response: object) -> None:
        """Attach response evidence already bounded by the target transport."""

        if self._closed:
            return
        self._closed = True
        response_detail = self._observer._safe(_response_detail(response))
        duration = round((time.monotonic() - self._started) * 1000, 2)
        event = self._observer._event_copy(self._event_id)
        if event is None:
            return
        detail = deepcopy(event.get("detail", {}))
        output = detail.get("output")
        output = deepcopy(output) if isinstance(output, dict) else {}
        output.update({"response": response_detail, "http_duration_ms": duration})
        detail["output"] = output
        processor_result = getattr(response, "processor_result", None)
        changes: dict[str, object] = {"detail": detail}
        if processor_result is not None and bool(
            getattr(processor_result, "warnings", ())
        ):
            changes["status"] = "warning"
        self._observer._update_event(self._event_id, **changes)

    def fail(self, exc: BaseException) -> None:
        """Attach a transport failure without changing the raised exception."""

        if self._closed:
            return
        self._closed = True
        error = {
            "type": type(exc).__name__,
            "message": self._observer._redactor.redact_text(str(exc)),
        }
        duration = round((time.monotonic() - self._started) * 1000, 2)
        stopped = isinstance(exc, KeyboardInterrupt)
        event = self._observer._event_copy(self._event_id)
        if event is None:
            return
        detail = deepcopy(event.get("detail", {}))
        output = detail.get("output")
        output = deepcopy(output) if isinstance(output, dict) else {}
        output.update({"transport_error": error, "http_duration_ms": duration})
        detail["output"] = output
        if stopped:
            detail["stopped"] = True
        self._observer._update_event(
            self._event_id,
            status="warning" if stopped else "failed",
            detail=detail,
        )
