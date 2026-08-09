"""Build the schema-v2 semantic narrative for the current RESTScope run.

The :class:`LiveRunObserver` receives the App's existing trace and target HTTP
activity. It folds that lower-level evidence into model turns, executed tools,
and complete Smoke Batches. Generic ``Agent.start`` and ``Agent.run`` scopes add
stable Main or Subagent identities and the authoritative final-response phase
used by the conversation projector. Browser adapters read JSON-safe snapshots
and cursor-addressed changes; workflow code never depends on UI DTOs.

The observer never persists data and never raises into testing code. It keeps
every detail until the next run or App shutdown, as explicitly approved, so a
very large run can consume substantial server and browser memory.
"""

from __future__ import annotations

import base64
import json
import time

from collections import Counter
from collections.abc import Mapping
from contextvars import ContextVar, Token
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Condition, RLock
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from restscope.redaction import Redactor


EventKind = Literal["agent_turn", "tool_call", "smoke_batch"]

_SMOKE_BATCH_SPAN = "OperationTestingService.run_smoke_batch"
_IGNORED_TOOL_SPANS = {"RESTScopeTestCase.execute"}
_PLAN_UPDATE_TOOL = "plan.update"
_HTTP_TOOL = "restscope.http.request"


@dataclass(frozen=True, slots=True)
class StreamChange:
    """One cursor-addressable update sent to connected browser clients.

    Args:
        cursor: Process-local monotonically increasing stream position.
        event_type: Stable client reducer operation.
        data: Complete replacement value for that operation.
    """

    cursor: int
    event_type: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return the wire representation used by snapshot and SSE adapters."""
        return {
            "cursor": self.cursor,
            "type": self.event_type,
            "data": deepcopy(self.data),
        }


@dataclass(frozen=True, slots=True)
class _ActiveContext:
    """Carry private aggregation ownership across nested and parallel spans."""

    event_id: str | None
    context_id: str | None
    agent: dict[str, Any] | None
    scope: dict[str, Any]


_CURRENT_CONTEXT: ContextVar[_ActiveContext] = ContextVar(
    "restscope_live_run_context",
    default=_ActiveContext(event_id=None, context_id=None, agent=None, scope={}),
)


class LiveRunObserver:
    """Own the complete current-run semantic timeline behind one Interface.

    Callers begin and end a run, while tracing and HTTP Modules open short-lived
    handles. Browser adapters call :meth:`snapshot` and :meth:`wait_after`.
    Locking, cursor management, semantic aggregation, redaction, Agent nesting,
    message de-duplication, and Main Agent Plan-to-Todo projection stay private
    here.
    """

    def __init__(self, *, redactor: Redactor | None = None) -> None:
        """Create an idle observer sharing the App's exact-value Redactor."""
        self._redactor = redactor or Redactor()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._events: dict[str, dict[str, Any]] = {}
        self._event_order: list[str] = []
        self._changes: list[StreamChange] = []
        self._cursor = 0
        self._next_order = 0
        self._run: dict[str, Any] | None = None
        self._todo: dict[str, Any] | None = None
        self._todo_revision = 0
        self._seen_message_counts: dict[str, Counter[str]] = {}
        self._latest_agent_turn: dict[str, str] = {}
        self._latest_agent_turn_by_task: dict[str, str] = {}
        self._agent_sessions: dict[tuple[Any, ...], str] = {}
        self._generic_agent_identities: dict[str, dict[str, Any]] = {}
        self._closed = False

    @property
    def active(self) -> bool:
        """Report whether a current run is accepting observation events."""
        with self._lock:
            return not self._closed and self._run is not None

    def begin_run(self, request: Any) -> str:
        """Replace prior run evidence and publish a new current-run identity.

        Args:
            request: The exact public run request. It is copied, JSON-normalized,
                and redacted before storage.

        Returns:
            A unique run ID used by snapshots and every timeline event.
        """
        run_id = f"run_{uuid4().hex}"
        now = _utc_now()
        with self._condition:
            if self._closed:
                return run_id
            self._events.clear()
            self._event_order.clear()
            self._todo = None
            self._todo_revision = 0
            self._seen_message_counts.clear()
            self._latest_agent_turn.clear()
            self._latest_agent_turn_by_task.clear()
            self._agent_sessions.clear()
            self._generic_agent_identities.clear()
            self._next_order = 0
            self._run = {
                "run_id": run_id,
                "status": "running",
                "started_at": now,
                "ended_at": None,
                "request": self._safe(request),
                "result": None,
            }
            # A reset has a cursor newer than anything held by an existing SSE
            # client, but old-run details are removed immediately.
            self._changes.clear()
            self._publish_locked("run.reset", deepcopy(self._run))
        return run_id

    def end_run(self, result: Any = None, *, error: BaseException | None = None) -> None:
        """Mark the current run terminal without allowing observer failure out.

        Args:
            result: Normal RESTScope report or another bounded terminal value.
            error: Unhandled run exception. Only its type and redacted message
                enter the observer; the original exception is never replaced.
        """
        try:
            with self._condition:
                if (
                    self._closed
                    or self._run is None
                    or self._run.get("status") != "running"
                ):
                    return
                if error is not None:
                    self._run["status"] = "errored"
                    self._run["result"] = {
                        "error_type": type(error).__name__,
                        "message": self._redactor.redact_text(str(error)),
                    }
                else:
                    safe_result = self._safe(result)
                    status = (
                        safe_result.get("status")
                        if isinstance(safe_result, dict)
                        else None
                    )
                    self._run["status"] = status or "completed"
                    self._run["result"] = safe_result
                self._run["ended_at"] = _utc_now()
                self._publish_locked("run.update", deepcopy(self._run))
        except Exception:
            return

    def interrupt_run(self) -> None:
        """Stop only the current Run and retain semantic cards and UI state.

        Any card still marked running is converted to a stopped warning. That
        terminal state is visually explicit but is not a business failure. The
        snapshot remains available until :meth:`begin_run` or :meth:`close`.
        """
        try:
            with self._condition:
                if (
                    self._closed
                    or self._run is None
                    or self._run.get("status") != "running"
                ):
                    return
                now = _utc_now()
                for event_id in self._event_order:
                    event = self._events.get(event_id)
                    if event is None or event.get("status") != "running":
                        continue
                    detail = deepcopy(event.get("detail", {}))
                    detail.update(
                        {
                            "stopped": True,
                            "stop_reason": "The caller stopped the current run.",
                        }
                    )
                    event.update(
                        {
                            "status": "warning",
                            "ended_at": now,
                            "detail": detail,
                            "revision": int(event.get("revision", 0)) + 1,
                        }
                    )
                    self._publish_locked("timeline.upsert", event)
                self._run["status"] = "stopped"
                self._run["result"] = {
                    "reason": "keyboard_interrupt",
                    "message": "The caller stopped the current run.",
                }
                self._run["ended_at"] = now
                self._publish_locked("run.update", deepcopy(self._run))
        except Exception:
            return

    def start_span(
        self,
        *,
        name: str,
        kind: str,
        input_value: Any | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> "LiveSpan | None":
        """Open one semantic event or an invisible aggregation context.

        Agent and helper spans provide ownership only. An LLM span under an
        Agent becomes one Agent-turn card, a real tool span becomes a Tool card,
        and the testing Batch span becomes a Smoke Batch card. Every other span
        stays invisible while still forwarding operation, round, and case scope.
        """
        try:
            if not self.active:
                return None
            parent = _CURRENT_CONTEXT.get()
            safe_attributes = self._safe(dict(attributes or {}))
            scope = _merge_scope(parent.scope, safe_attributes)
            context_id = f"context_{uuid4().hex}"
            event: dict[str, Any] | None = None
            agent = parent.agent
            visible_parent_id = parent.event_id

            if name in {"Agent.run", "Agent.start"} and kind == "CHAIN":
                agent, scope = self._generic_agent_task(
                    parent=parent,
                    attributes=safe_attributes,
                    scope=scope,
                    input_value=input_value,
                )
            elif kind == "AGENT":
                agent = self._agent_identity(
                    name=name,
                    parent=parent,
                    scope=scope,
                    context_id=context_id,
                )
            elif kind == "LLM" and agent is not None:
                event_id = f"event_{uuid4().hex}"
                event = self._new_event(
                    event_id=event_id,
                    kind="agent_turn",
                    name=str(agent["name"]),
                    parent_event_id=visible_parent_id,
                    agent=agent,
                    scope=scope,
                    input_value=None,
                    attributes={},
                )
                event["detail"] = {
                    "input": {"messages": []},
                    "output": None,
                    "phase": "commentary",
                    **(
                        {
                            "task": {
                                "task_id": scope["task_id"],
                                "objective": scope.get("task_objective"),
                            }
                        }
                        if scope.get("task_id") is not None
                        else {}
                    ),
                }
                self._upsert(event)
                self._latest_agent_turn[str(agent["session_id"])] = event_id
                if scope.get("task_id") is not None:
                    self._latest_agent_turn_by_task[str(scope["task_id"])] = event_id
                visible_parent_id = event_id
            elif kind == "TOOL" and name not in _IGNORED_TOOL_SPANS:
                event_id = f"event_{uuid4().hex}"
                if agent is not None:
                    visible_parent_id = self._latest_agent_turn.get(
                        str(agent["session_id"]),
                        visible_parent_id,
                    )
                safe_attributes.setdefault(
                    "restscope.tool.family",
                    classify_tool(name),
                )
                event = self._new_event(
                    event_id=event_id,
                    kind="tool_call",
                    name=name,
                    parent_event_id=visible_parent_id,
                    agent=agent,
                    scope=scope,
                    input_value=input_value,
                    attributes=safe_attributes,
                )
                self._upsert(event)
                visible_parent_id = event_id
            elif name == _SMOKE_BATCH_SPAN:
                event_id = f"event_{uuid4().hex}"
                event = self._new_event(
                    event_id=event_id,
                    kind="smoke_batch",
                    name=name,
                    parent_event_id=visible_parent_id,
                    agent=agent,
                    scope=scope,
                    input_value=None,
                    attributes=safe_attributes,
                )
                safe_input = self._safe(input_value) if input_value is not None else {}
                initial = safe_input if isinstance(safe_input, dict) else {"input": safe_input}
                event["detail"] = {**initial, "cases": []}
                self._upsert(event)
                visible_parent_id = event_id

            token = _CURRENT_CONTEXT.set(
                _ActiveContext(
                    event_id=visible_parent_id,
                    context_id=context_id,
                    agent=agent,
                    scope=scope,
                )
            )
            return LiveSpan(
                observer=self,
                event_id=event["event_id"] if event is not None else None,
                context_token=token,
                span_name=name,
                task_id=(
                    str(scope["task_id"])
                    if scope.get("task_id") is not None
                    else None
                ),
                is_agent_run=name in {"Agent.run", "Agent.start"} and kind == "CHAIN",
            )
        except Exception:
            return None

    def start_http_exchange(
        self,
        *,
        method: str,
        path: str,
        url: str,
        headers: Mapping[str, str],
        request_kwargs: Mapping[str, Any] | None,
        operation_key: str | None,
        path_template: str | None,
    ) -> "LiveHTTPExchange | None":
        """Attach one final prepared target request to its semantic owner.

        A request under ``restscope.http.request`` enriches that Tool card. A
        generated Test Case appends one row to its Smoke Batch. Requests outside
        either semantic context intentionally remain absent from the timeline.
        """
        try:
            if not self.active:
                return None
            parent = _CURRENT_CONTEXT.get()
            owner = self._event_copy(parent.event_id)
            if owner is None:
                return None
            request = self._safe(
                {
                    "method": method.upper(),
                    "path": path,
                    "path_template": path_template,
                    "url": url,
                    "query": [
                        {"name": item_name, "value": value}
                        for item_name, value in parse_qsl(
                            urlsplit(url).query,
                            keep_blank_values=True,
                        )
                    ],
                    "headers": dict(headers),
                    "body": _request_body(request_kwargs or {}),
                }
            )
            if owner.get("kind") == "tool_call" and owner.get("name") == _HTTP_TOOL:
                detail = deepcopy(owner.get("detail", {}))
                tool_input = detail.get("input")
                if not isinstance(tool_input, dict):
                    tool_input = {"arguments": tool_input}
                tool_input["request"] = request
                detail["input"] = tool_input
                self._update_event(str(owner["event_id"]), detail=detail)
                return LiveHTTPExchange(
                    observer=self,
                    event_id=str(owner["event_id"]),
                    target="tool",
                )
            if owner.get("kind") == "smoke_batch":
                case_index = parent.scope.get("case_index")
                if not isinstance(case_index, int):
                    case_index = len(owner.get("detail", {}).get("cases", []))
                case_id = parent.scope.get("case_id")
                case = {
                    "case_index": case_index,
                    "case_id": str(case_id or f"TC{case_index + 1}"),
                    "run_id": parent.scope.get("test_run_id"),
                    "method": method.upper(),
                    "url": url,
                    "status": "running",
                    "duration_ms": None,
                    "request": request,
                    "response": None,
                    "transport_error": None,
                }
                self._replace_batch_case(
                    event_id=str(owner["event_id"]),
                    case_index=case_index,
                    case=case,
                )
                return LiveHTTPExchange(
                    observer=self,
                    event_id=str(owner["event_id"]),
                    target="smoke_batch",
                    case_index=case_index,
                )
            return None
        except Exception:
            return None

    def snapshot(self) -> dict[str, Any]:
        """Return one atomic schema-v2 browser snapshot of the current run."""
        with self._lock:
            return {
                "schema_version": 2,
                "run": deepcopy(self._run),
                "events": [
                    deepcopy(self._events[event_id])
                    for event_id in self._event_order
                    if event_id in self._events
                ],
                "todo": deepcopy(self._todo),
                "latest_cursor": self._cursor,
            }

    def wait_after(
        self,
        cursor: int,
        timeout_seconds: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Wait for SSE changes newer than ``cursor`` without polling the App."""
        with self._condition:
            if not self._changes or self._changes[-1].cursor <= cursor:
                self._condition.wait(timeout=max(0.0, timeout_seconds))
            return [
                change.as_dict()
                for change in self._changes
                if change.cursor > cursor
            ]

    def close(self) -> None:
        """Clear all raw run evidence and wake any waiting SSE adapter."""
        with self._condition:
            self._closed = True
            self._events.clear()
            self._event_order.clear()
            self._changes.clear()
            self._run = None
            self._todo = None
            self._todo_revision = 0
            self._seen_message_counts.clear()
            self._latest_agent_turn.clear()
            self._latest_agent_turn_by_task.clear()
            self._agent_sessions.clear()
            self._generic_agent_identities.clear()
            self._condition.notify_all()

    def _agent_identity(
        self,
        *,
        name: str,
        parent: _ActiveContext,
        scope: Mapping[str, Any],
        context_id: str,
    ) -> dict[str, Any]:
        """Return a stable session identity for one Agent conversation.

        Repeated Agent method spans beneath one long-lived Coordinator belong to
        one session. Operation and round prevent unrelated conversations under
        that Coordinator from sharing prompt-delta state. Nested Agents also
        retain their exact parent session so the read-only canvas can preserve
        a direct call when no visible Tool or Agent-turn event owns that hop.
        """
        path = [*(parent.agent.get("path", []) if parent.agent else []), name]
        parent_session_id = (
            str(parent.agent["session_id"])
            if parent.agent is not None and parent.agent.get("session_id") is not None
            else None
        )
        if parent.context_id is None:
            session_id = f"agent_{uuid4().hex}"
        else:
            key = (
                parent.context_id,
                name,
                scope.get("operation_key"),
                scope.get("round_number"),
            )
            session_id = self._agent_sessions.setdefault(
                key,
                f"agent_{uuid4().hex}",
            )
        identity = {"session_id": session_id, "name": name, "path": path}
        # Root Agents keep their existing compact wire shape. Only a true
        # nested session needs the additive relationship field.
        if parent_session_id is not None:
            identity["parent_session_id"] = parent_session_id
        return identity

    def _generic_agent_task(
        self,
        *,
        parent: _ActiveContext,
        attributes: Mapping[str, Any],
        scope: dict[str, Any],
        input_value: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create one explicit generic Agent identity and task scope.

        Generic Main and Subagent sessions already supply their Harness-owned
        IDs in generic Agent lifecycle attributes. Unlike legacy ``kind=AGENT``
        spans, these identities are authoritative enough for the UI to select a
        Main conversation and navigate its children without guessing names.
        """
        session_id = str(attributes.get("restscope.agent.session_id") or "")
        profile_name = str(attributes.get("restscope.agent.profile") or "")
        lifecycle = str(attributes.get("restscope.agent.lifecycle") or "")
        if not session_id or not profile_name or lifecycle not in {"main", "subagent"}:
            return parent.agent or {}, scope

        parent_session_value = attributes.get("restscope.agent.parent_session_id")
        parent_session_id = (
            str(parent_session_value) if parent_session_value is not None else None
        )
        parent_identity = self._generic_agent_identities.get(parent_session_id or "")
        parent_path = parent_identity.get("path", []) if parent_identity else []
        task_id = f"task_{uuid4().hex}"
        identity: dict[str, Any] = {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
            "name": profile_name,
            "profile_name": profile_name,
            "lifecycle": lifecycle,
            "task_id": task_id,
            "path": [*parent_path, profile_name],
        }
        self._generic_agent_identities[session_id] = deepcopy(identity)
        safe_input = self._safe(input_value)
        objective = safe_input.get("objective") if isinstance(safe_input, dict) else None
        return identity, {
            **scope,
            "task_id": task_id,
            "task_objective": objective,
        }

    def _complete_agent_task(self, *, task_id: str, output: Any) -> None:
        """Correct the successful task's last model turn to Final Answer.

        A no-tool model response is only a candidate until the generic Agent
        validates its schema. The enclosing ``Agent.run`` result is therefore
        the sole authority that can promote the last response. Failed,
        cancelled, stopped, and validation-correction tasks remain commentary.
        """
        safe_output = self._safe(output)
        if not isinstance(safe_output, dict) or safe_output.get("status") != "completed":
            return
        event_id = self._latest_agent_turn_by_task.get(task_id)
        event = self._event_copy(event_id)
        if event is None:
            return
        detail = deepcopy(event.get("detail", {}))
        detail["phase"] = "final_answer"
        detail["task_result"] = safe_output
        self._update_event(str(event["event_id"]), detail=detail)

    def _new_event(
        self,
        *,
        event_id: str,
        kind: EventKind,
        name: str,
        parent_event_id: str | None,
        agent: dict[str, Any] | None,
        scope: dict[str, Any],
        input_value: Any,
        attributes: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Allocate one event in immutable start order before it can finish."""
        with self._lock:
            self._next_order += 1
            order = self._next_order
            run_id = self._run["run_id"] if self._run is not None else None
        return {
            "event_id": event_id,
            "run_id": run_id,
            "order": order,
            "revision": 1,
            "kind": kind,
            "name": name,
            "status": "running",
            "started_at": _utc_now(),
            "ended_at": None,
            "duration_ms": None,
            "parent_event_id": parent_event_id,
            "agent": deepcopy(agent),
            "operation_key": scope.get("operation_key"),
            "round_number": scope.get("round_number"),
            "summary": _event_summary(kind=kind, name=name, attributes=attributes),
            "attributes": self._safe(attributes),
            "detail": (
                {"input": self._safe(input_value)}
                if input_value is not None
                else {}
            ),
        }

    def _upsert(self, event: dict[str, Any]) -> None:
        """Store and publish one complete event replacement without raising."""
        try:
            with self._condition:
                event_id = str(event["event_id"])
                if event_id not in self._events:
                    self._event_order.append(event_id)
                self._events[event_id] = deepcopy(event)
                self._publish_locked("timeline.upsert", event)
        except Exception:
            return

    def _update_event(self, event_id: str, **changes: Any) -> dict[str, Any] | None:
        """Apply one in-place semantic update while retaining original order."""
        try:
            with self._condition:
                event = self._events.get(event_id)
                if event is None:
                    return None
                event.update(deepcopy(changes))
                event["revision"] = int(event.get("revision", 0)) + 1
                self._publish_locked("timeline.upsert", event)
                return deepcopy(event)
        except Exception:
            return None

    def _event_copy(self, event_id: str | None) -> dict[str, Any] | None:
        """Read one event safely for a later copy-on-write update."""
        if event_id is None:
            return None
        with self._lock:
            event = self._events.get(event_id)
            return deepcopy(event) if event is not None else None

    def _publish_locked(self, event_type: str, data: dict[str, Any]) -> None:
        """Append one stream change while the observer condition is held."""
        self._cursor += 1
        self._changes.append(
            StreamChange(
                cursor=self._cursor,
                event_type=event_type,
                data=deepcopy(data),
            )
        )
        self._condition.notify_all()

    def _safe(self, value: Any) -> Any:
        """Convert a value to JSON data using the App's exact-value policy."""
        return self._redactor.redact(value)

    def _set_agent_messages(
        self,
        *,
        event_id: str,
        direction: str,
        messages: list[dict[str, Any]],
        summary: Any,
    ) -> None:
        """Fold exact LLM messages into one user-facing Agent turn.

        The first input stores system and user messages. Later full prompts are
        compared with all messages already visible in this Agent session, so the
        card receives every newly added tool result and harness feedback without
        repeating earlier history. Output always describes this response only.
        """
        event = self._event_copy(event_id)
        if event is None or event.get("kind") != "agent_turn":
            return
        agent = event.get("agent")
        session_id = (
            str(agent.get("session_id"))
            if isinstance(agent, dict) and agent.get("session_id")
            else event_id
        )
        safe_messages = [
            item
            for item in self._safe(messages)
            if isinstance(item, dict)
        ]
        safe_summary = self._safe(summary)
        detail = deepcopy(event.get("detail", {}))
        with self._lock:
            seen = self._seen_message_counts.setdefault(session_id, Counter())
            if direction == "input":
                prompt_counts: Counter[str] = Counter()
                new_messages: list[dict[str, Any]] = []
                for message in safe_messages:
                    fingerprint = _message_fingerprint(message)
                    prompt_counts[fingerprint] += 1
                    if prompt_counts[fingerprint] > seen[fingerprint]:
                        new_messages.append(message)
                for fingerprint, count in prompt_counts.items():
                    seen[fingerprint] = max(seen[fingerprint], count)
                detail["input"] = {"messages": new_messages}
            else:
                for message in safe_messages:
                    seen[_message_fingerprint(message)] += 1
                assistant = next(
                    (
                        message
                        for message in safe_messages
                        if message.get("role") == "assistant"
                    ),
                    safe_messages[0] if safe_messages else {},
                )
                exact_tool_calls: list[Any] = []
                for message in safe_messages:
                    calls = message.get("tool_calls")
                    if isinstance(calls, list):
                        exact_tool_calls.extend(deepcopy(calls))
                summary_mapping = (
                    safe_summary if isinstance(safe_summary, dict) else {}
                )
                detail["output"] = {
                    "messages": safe_messages,
                    "content": assistant.get("content"),
                    "structured": summary_mapping.get("parsed_json"),
                    "finish_reason": summary_mapping.get("finish_reason"),
                    "tool_calls": exact_tool_calls,
                }
        self._update_event(event_id, detail=detail)

    def _set_event_detail_value(self, event_id: str, name: str, value: Any) -> None:
        """Add observer-only detail without changing exported Phoenix fields."""
        event = self._event_copy(event_id)
        if event is None:
            return
        detail = deepcopy(event.get("detail", {}))
        detail[name] = self._safe(value)
        self._update_event(event_id, detail=detail)

    def _replace_batch_case(
        self,
        *,
        event_id: str,
        case_index: int,
        case: Mapping[str, Any],
    ) -> None:
        """Insert or update one case by stable Batch index and keep row order."""
        event = self._event_copy(event_id)
        if event is None or event.get("kind") != "smoke_batch":
            return
        detail = deepcopy(event.get("detail", {}))
        cases = [item for item in detail.get("cases", []) if isinstance(item, dict)]
        replaced = False
        for index, current in enumerate(cases):
            if current.get("case_index") == case_index:
                cases[index] = self._safe(case)
                replaced = True
                break
        if not replaced:
            cases.append(self._safe(case))
        cases.sort(key=lambda item: int(item.get("case_index", 0)))
        detail["cases"] = cases
        self._update_event(event_id, detail=detail)

    def _record_todo(self, tool_event: dict[str, Any]) -> None:
        """Project one successful Main Agent Plan replacement as the floating Todo.

        Subagents own independent private Plans. A single page-level Todo must
        therefore follow only the explicit Main Agent instead of allowing a
        short-lived child to overwrite its parent's current work.
        """
        agent = tool_event.get("agent")
        if not isinstance(agent, dict) or agent.get("lifecycle") != "main":
            return
        detail = tool_event.get("detail")
        output = detail.get("output") if isinstance(detail, dict) else None
        if not isinstance(output, dict) or output.get("status") != "succeeded":
            return
        plan = output.get("structured")
        if not isinstance(plan, dict) or not isinstance(plan.get("plan"), list):
            return
        items = [
            deepcopy(item)
            for item in plan["plan"]
            if isinstance(item, dict)
            and isinstance(item.get("step"), str)
            and item.get("status") in {"pending", "in_progress", "completed"}
        ]
        completed_count = sum(item["status"] == "completed" for item in items)
        active_step = next(
            (item["step"] for item in items if item["status"] == "in_progress"),
            None,
        )
        with self._condition:
            self._todo_revision += 1
            self._todo = {
                "revision": self._todo_revision,
                "agent": deepcopy(agent),
                "explanation": deepcopy(plan.get("explanation")),
                "items": items,
                "completed_count": completed_count,
                "total_count": len(items),
                "active_step": active_step,
                "percent": round(completed_count * 100 / len(items)) if items else 0,
            }
            self._publish_locked("todo.replace", deepcopy(self._todo))


class LiveSpan:
    """One best-effort handle that updates a semantic event or hidden context."""

    def __init__(
        self,
        *,
        observer: LiveRunObserver,
        event_id: str | None,
        context_token: Token[_ActiveContext],
        span_name: str,
        task_id: str | None,
        is_agent_run: bool,
    ) -> None:
        """Remember event, Agent task, nesting token, and elapsed-time start."""
        self._observer = observer
        self._event_id = event_id
        self._context_token = context_token
        self._span_name = span_name
        self._task_id = task_id
        self._is_agent_run = is_agent_run
        self._started = time.monotonic()
        self._closed = False

    def set_content(self, direction: str, value: Any) -> None:
        """Store semantic input/output while preserving nested HTTP evidence."""
        if (
            self._is_agent_run
            and direction == "output"
            and self._task_id is not None
        ):
            self._observer._complete_agent_task(
                task_id=self._task_id,
                output=value,
            )
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
                tool_input = safe_value if isinstance(safe_value, dict) else {"arguments": safe_value}
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
        if self._event_id is None:
            return
        self._observer._set_agent_messages(
            event_id=self._event_id,
            direction=direction,
            messages=messages,
            summary=summary,
        )

    def set_attribute(self, name: str, value: Any) -> None:
        """Add semantic scope/status without exposing LLM provider metadata."""
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
        """Store observer-only detail that must not change Phoenix output."""
        if self._event_id is not None:
            self._observer._set_event_detail_value(self._event_id, name, value)

    def mark_error(self, message: str) -> None:
        """Mark one semantic event failed using a redacted safe message."""
        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        detail = deepcopy(event.get("detail", {})) if event else {}
        detail["error"] = self._observer._redactor.redact_text(message)
        self._observer._update_event(
            self._event_id,
            status="failed",
            detail=detail,
        )

    def mark_interrupted(self) -> None:
        """Mark caller cancellation as a stopped warning, not a business failure."""
        if self._event_id is None:
            return
        event = self._observer._event_copy(self._event_id)
        detail = deepcopy(event.get("detail", {})) if event else {}
        detail.update(
            {
                "stopped": True,
                "stop_reason": "The caller stopped the current run.",
            }
        )
        self._observer._update_event(
            self._event_id,
            status="warning",
            detail=detail,
        )

    def mark_ok(self) -> None:
        """Mark an unfinished event successful without hiding prior status."""
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
                    else event.get("status", "succeeded") if event else "succeeded"
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


class LiveHTTPExchange:
    """Complete one HTTP Tool or Smoke Batch case with bounded evidence."""

    def __init__(
        self,
        *,
        observer: LiveRunObserver,
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
        """Attach response evidence already bounded and read by the transport."""
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
        self._finish_batch_case(
            response=response_detail,
            duration_ms=duration,
        )

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
            output.update(
                {
                    "transport_error": error,
                    "http_duration_ms": duration,
                }
            )
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


def classify_tool(name: str) -> str:
    """Map a concrete tool name to one stable visual family."""
    if name.startswith("failure_resolution."):
        return "worklist"
    if name.startswith("plan."):
        return "plan"
    if name.startswith("openapi."):
        return "openapi"
    if name.startswith("test_case."):
        return "test_case"
    if name == "lookup_parameter_history" or "parameter_patch" in name:
        return "parameter_patch"
    if name.startswith("resource."):
        return "resource"
    if name == _HTTP_TOOL:
        return "http"
    if name.startswith("mcp."):
        return "mcp"
    return "other"


def _event_summary(
    *,
    kind: EventKind,
    name: str,
    attributes: Mapping[str, Any],
) -> str:
    """Build the short redundant label shown while a card is collapsed."""
    if kind == "agent_turn":
        return f"Agent turn · {name}"
    if kind == "tool_call":
        return f"{classify_tool(name).replace('_', ' ').title()} · {name}"
    count = attributes.get("restscope.test.case_count")
    return f"Smoke Batch · {count} cases" if count is not None else "Smoke Batch"


def _merge_scope(parent: Mapping[str, Any], attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Propagate only identifiers needed to own semantic cards and Batch rows."""
    scope = dict(parent)
    mappings = {
        "restscope.operation.key": "operation_key",
        "restscope.operation.round": "round_number",
        "restscope.test.run_id": "test_run_id",
        "restscope.test.case_id": "case_id",
        "restscope.test.case_index": "case_index",
    }
    for source, destination in mappings.items():
        if attributes.get(source) is not None:
            scope[destination] = attributes[source]
    return scope


def _semantic_status(
    *,
    event: Mapping[str, Any],
    output: Any,
    direction: str,
) -> str | None:
    """Derive visible Tool and Batch status from their completed output."""
    if direction != "output" or not isinstance(output, dict):
        return None
    if event.get("kind") == "tool_call":
        status = output.get("status")
        return _tool_status(str(status)) if status is not None else None
    if event.get("kind") == "smoke_batch":
        success_count = output.get("success_count")
        case_count = output.get("case_count")
        if isinstance(success_count, int) and isinstance(case_count, int):
            if success_count == case_count:
                return "succeeded"
            if success_count == 0:
                return "failed"
            return "warning"
    return None


def _tool_status(status: str) -> str:
    """Map ToolResult status to the four visual event states."""
    if status in {"failed", "timed_out"}:
        return "failed"
    if status in {"denied", "warning"}:
        return "warning"
    return "succeeded"


def _message_fingerprint(message: Mapping[str, Any]) -> str:
    """Produce a stable comparison key for repeated full prompt snapshots."""
    return json.dumps(message, ensure_ascii=False, sort_keys=True, default=str)


def _request_body(request_kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project the transport's one selected body encoding for safe display."""
    for key in ("json", "content", "data"):
        if key not in request_kwargs:
            continue
        value = request_kwargs[key]
        if isinstance(value, bytes | bytearray):
            return _decode_body(bytes(value), media_type=None, encoding="utf-8")
        return {"format": "json" if key == "json" else "text", "value": value}
    return None


def _response_detail(response: Any) -> dict[str, Any]:
    """Convert one already-bounded response into JSON/text/Base64 evidence."""
    headers = dict(getattr(response, "headers", {}) or {})
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    body = getattr(response, "body", None)
    retained_size = len(body) if isinstance(body, bytes | bytearray) else None
    size_bytes = _reported_response_size(headers, retained_size)
    return {
        "status_code": getattr(response, "status_code", None),
        "reason_phrase": getattr(response, "reason_phrase", ""),
        "url": getattr(response, "url", ""),
        "headers": headers,
        "body": (
            _decode_body(
                bytes(body),
                media_type=media_type or None,
                encoding=getattr(response, "encoding", None) or "utf-8",
            )
            if isinstance(body, bytes | bytearray)
            else None
        ),
        "body_retained": body is not None,
        "body_truncated": bool(getattr(response, "body_truncated", False)),
        "size_bytes": size_bytes,
        "retained_size_bytes": retained_size,
        "processor_result": getattr(response, "processor_result", None),
    }


def _reported_response_size(
    headers: Mapping[str, Any],
    retained_size: int | None,
) -> int | None:
    """Prefer a valid Content-Length while retaining the exact stored byte count."""
    raw = next(
        (
            value
            for name, value in headers.items()
            if str(name).casefold() == "content-length"
        ),
        None,
    )
    try:
        return int(raw) if raw is not None else retained_size
    except (TypeError, ValueError):
        return retained_size


def _decode_body(content: bytes, *, media_type: str | None, encoding: str) -> dict[str, Any]:
    """Decode bounded bytes without pretending binary evidence is text."""
    if media_type == "application/json" or (media_type or "").endswith("+json"):
        try:
            return {"format": "json", "value": json.loads(content.decode(encoding))}
        except (LookupError, UnicodeDecodeError, json.JSONDecodeError):
            pass
    textual = (
        media_type is None
        or media_type.startswith("text/")
        or media_type.endswith("+xml")
        or media_type
        in {
            "application/graphql",
            "application/javascript",
            "application/x-www-form-urlencoded",
            "application/x-yaml",
            "application/xml",
            "application/yaml",
        }
    )
    if textual:
        try:
            return {"format": "text", "value": content.decode(encoding)}
        except (LookupError, UnicodeDecodeError):
            pass
    return {
        "format": "base64",
        "value": base64.b64encode(content).decode("ascii"),
    }


def _utc_now() -> str:
    """Return one timezone-explicit timestamp suitable for lexical display."""
    return datetime.now(UTC).isoformat()
