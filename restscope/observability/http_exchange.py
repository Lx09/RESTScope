"""Attach one bounded target HTTP exchange to its live Tool or Batch event."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import time
from typing import Any, Literal, TYPE_CHECKING

from .projection import response_detail as _response_detail

if TYPE_CHECKING:
    from .observer import LiveRunObserver


class LiveHTTPExchange:
    """Complete one HTTP Tool or Smoke Batch case with bounded evidence."""

    def __init__(
        self,
        *,
        observer: "LiveRunObserver",
        event_id: str,
        target: Literal["tool", "smoke_batch"],
        case_index: int | None = None,
    ) -> None:
        """Remember the semantic owner and optional Batch case index."""

        self._observer = observer
        self._event_id = event_id
        self._target = target
        self._case_index = case_index
        self._started = time.monotonic()
        self._closed = False

    def finish(self, response: Any) -> None:
        """Attach response evidence already bounded by the target transport."""

        if self._closed:
            return
        self._closed = True
        response_detail = self._observer._safe(_response_detail(response))
        duration = round((time.monotonic() - self._started) * 1000, 2)
        if self._target == "tool":
            event = self._observer._event_copy(self._event_id)
            if event is None:
                return
            detail = deepcopy(event.get("detail", {}))
            output = detail.get("output")
            output = deepcopy(output) if isinstance(output, dict) else {}
            output.update({"response": response_detail, "http_duration_ms": duration})
            detail["output"] = output
            processor_result = getattr(response, "processor_result", None)
            changes: dict[str, Any] = {"detail": detail}
            if processor_result is not None and bool(
                getattr(processor_result, "warnings", ())
            ):
                changes["status"] = "warning"
            self._observer._update_event(self._event_id, **changes)
            return
        self._finish_batch_case(response=response_detail, duration_ms=duration)

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
        if self._target == "tool":
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
            return
        self._finish_batch_case(
            response=None,
            duration_ms=duration,
            error=error,
            stopped=stopped,
        )

    def _finish_batch_case(
        self,
        *,
        response: Mapping[str, Any] | None,
        duration_ms: float,
        error: Mapping[str, Any] | None = None,
        stopped: bool = False,
    ) -> None:
        """Update exactly one Batch row after an HTTP response or failure."""

        if self._case_index is None:
            return
        event = self._observer._event_copy(self._event_id)
        if event is None:
            return
        cases = event.get("detail", {}).get("cases", [])
        current = next(
            (
                deepcopy(case)
                for case in cases
                if isinstance(case, dict)
                and case.get("case_index") == self._case_index
            ),
            None,
        )
        if current is None:
            return
        status_code = response.get("status_code") if response is not None else None
        success = isinstance(status_code, int) and 200 <= status_code < 300
        current.update(
            {
                "status": (
                    "warning" if stopped else "succeeded" if success else "failed"
                ),
                "duration_ms": duration_ms,
                "response": deepcopy(response),
                "transport_error": deepcopy(error),
            }
        )
        if stopped:
            current["stopped"] = True
        self._observer._replace_batch_case(
            event_id=self._event_id,
            case_index=self._case_index,
            case=current,
        )
