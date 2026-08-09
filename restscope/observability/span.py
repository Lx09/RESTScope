"""Project one traced runtime span into the current live semantic event.

``LiveSpan`` is a best-effort handle returned by :class:`LiveRunObserver`. It
updates Agent, Tool, and Smoke Batch cards while keeping observer state,
locking, and cursor publication inside the observer module.
"""

from __future__ import annotations

from copy import deepcopy
import time
from typing import Any, TYPE_CHECKING
from contextvars import Token

from .observer import (
    _ActiveContext,
    _CURRENT_CONTEXT,
    _HTTP_TOOL,
    _PLAN_UPDATE_TOOL,
    _semantic_status,
    _tool_status,
    _utc_now,
)

if TYPE_CHECKING:
    from .observer import LiveRunObserver


class LiveSpan:
    """Update one semantic event or hidden nesting context without raising."""

    def __init__(
        self,
        *,
        observer: "LiveRunObserver",
        event_id: str | None,
        context_token: Token[_ActiveContext],
        span_name: str,
        task_id: str | None,
        is_agent_run: bool,
    ) -> None:
        """Remember event ownership, nesting token, and elapsed-time start."""

        self._observer = observer
        self._event_id = event_id
        self._context_token = context_token
        self._span_name = span_name
        self._task_id = task_id
        self._is_agent_run = is_agent_run
        self._started = time.monotonic()
        self._closed = False

    def set_content(self, direction: str, value: Any) -> None:
        """Store semantic input or output while preserving nested HTTP evidence."""

        if self._is_agent_run and direction == "output" and self._task_id is not None:
            self._observer._complete_agent_task(task_id=self._task_id, output=value)
        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        if event is None:
            return
        safe_value = self._observer._safe(value)
        detail = deepcopy(event.get("detail", {}))
        if event.get("kind") == "smoke_batch" and direction == "output":
            if isinstance(safe_value, dict):
                detail.update(safe_value)
            else:
                detail["output"] = safe_value
        elif event.get("kind") == "tool_call" and event.get("name") == _HTTP_TOOL:
            if direction == "input":
                request = (
                    detail.get("input", {}).get("request")
                    if isinstance(detail.get("input"), dict)
                    else None
                )
                tool_input = (
                    safe_value
                    if isinstance(safe_value, dict)
                    else {"arguments": safe_value}
                )
                if request is not None:
                    tool_input["request"] = request
                detail["input"] = tool_input
            else:
                output = detail.get("output")
                output = deepcopy(output) if isinstance(output, dict) else {}
                output["tool_result"] = safe_value
                detail["output"] = output
        else:
            detail[direction] = safe_value
        changes: dict[str, Any] = {"detail": detail}
        status = _semantic_status(event=event, output=safe_value, direction=direction)
        if status is not None:
            changes["status"] = status
        self._observer._update_event(self._event_id, **changes)

    def set_messages(
        self,
        direction: str,
        messages: list[dict[str, Any]],
        *,
        summary: Any,
    ) -> None:
        """Store one Agent turn's incremental input or exact assistant output."""

        if self._event_id is not None:
            self._observer._set_agent_messages(
                event_id=self._event_id,
                direction=direction,
                messages=messages,
                summary=summary,
            )

    def set_attribute(self, name: str, value: Any) -> None:
        """Add semantic scope or status without provider metadata."""

        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        if event is None:
            return
        changes: dict[str, Any] = {}
        if event.get("kind") != "agent_turn":
            attributes = deepcopy(event.get("attributes", {}))
            attributes[name] = self._observer._safe(value)
            changes["attributes"] = attributes
        if name == "restscope.operation.key":
            changes["operation_key"] = self._observer._safe(value)
        elif name == "restscope.operation.round":
            changes["round_number"] = self._observer._safe(value)
        elif name == "restscope.test.run_id" and event.get("kind") == "smoke_batch":
            detail = deepcopy(event.get("detail", {}))
            detail["run_id"] = self._observer._safe(value)
            changes["detail"] = detail
        elif name == "restscope.tool.status" and event.get("kind") == "tool_call":
            changes["status"] = _tool_status(str(value))
        if changes:
            self._observer._update_event(self._event_id, **changes)

    def set_detail(self, name: str, value: Any) -> None:
        """Store observer-only detail that must not change tracing output."""

        if self._event_id is not None:
            self._observer._set_event_detail_value(self._event_id, name, value)

    def mark_error(self, message: str) -> None:
        """Mark the event failed using a redacted safe message."""

        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        detail = deepcopy(event.get("detail", {})) if event else {}
        detail["error"] = self._observer._redactor.redact_text(message)
        self._observer._update_event(self._event_id, status="failed", detail=detail)

    def mark_interrupted(self) -> None:
        """Mark caller cancellation as a stopped warning, not a business failure."""

        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        detail = deepcopy(event.get("detail", {})) if event else {}
        detail.update(
            {"stopped": True, "stop_reason": "The caller stopped the current run."}
        )
        self._observer._update_event(self._event_id, status="warning", detail=detail)

    def mark_ok(self) -> None:
        """Mark an unfinished event successful without hiding a prior status."""

        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        if event is not None and event.get("status") == "running":
            self._observer._update_event(self._event_id, status="succeeded")

    def finish(self) -> None:
        """Close the live context and publish duration exactly once."""

        if self._closed:
            return
        self._closed = True
        try:
            if self._event_id is not None:
                event = self._observer._event_copy(self._event_id)
                status = (
                    "succeeded"
                    if event is not None and event.get("status") == "running"
                    else event.get("status", "succeeded")
                    if event
                    else "succeeded"
                )
                updated = self._observer._update_event(
                    self._event_id,
                    status=status,
                    ended_at=_utc_now(),
                    duration_ms=round((time.monotonic() - self._started) * 1000, 2),
                )
                if updated is not None and self._span_name == _PLAN_UPDATE_TOOL:
                    self._observer._record_todo(updated)
        finally:
            try:
                _CURRENT_CONTEXT.reset(self._context_token)
            except Exception:
                pass
