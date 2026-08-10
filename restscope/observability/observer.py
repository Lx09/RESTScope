"""Build the schema-v3 semantic narrative for the current RESTScope run.

The :class:`LiveRunObserver` receives the App's existing trace and target HTTP
activity. It folds that lower-level evidence into model turns and executed
tools. Generic ``Agent.start`` and ``Agent.run`` scopes add stable Main,
Subagent, or System Agent identities and the authoritative final-response phase
used by the conversation projector. Browser adapters read JSON-safe snapshots
and cursor-addressed changes; workflow code never depends on UI DTOs.

The observer never persists data and never raises into testing code. It keeps
every detail until the next run or App shutdown, as explicitly approved, so a
very large run can consume substantial server and browser memory.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from threading import Condition, RLock
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from .redaction import Redactor

from .projection import (
    EventKind,
    HTTP_TOOL_NAME,
    classify_tool,
    event_summary as _event_summary,
    merge_scope as _merge_scope,
    message_fingerprint as _message_fingerprint,
    request_body as _request_body,
    semantic_status as _semantic_status,
    tool_status as _tool_status,
    utc_now as _utc_now,
)

_IGNORED_TOOL_SPANS = {"RESTScopeTestCase.execute"}
_PLAN_UPDATE_TOOL = "plan.update"
_HTTP_TOOL = HTTP_TOOL_NAME


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
    data: dict[str, object]

    def as_dict(self) -> dict[str, object]:
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
    agent: dict[str, object] | None
    scope: dict[str, object]


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
        self._events: dict[str, dict[str, object]] = {}
        self._event_order: list[str] = []
        self._changes: list[StreamChange] = []
        self._cursor = 0
        self._next_order = 0
        self._run: dict[str, object] | None = None
        self._todo: dict[str, object] | None = None
        self._todo_revision = 0
        self._seen_message_counts: dict[str, Counter[str]] = {}
        self._latest_agent_turn: dict[str, str] = {}
        self._latest_agent_turn_by_task: dict[str, str] = {}
        self._agent_sessions: dict[tuple[object, ...], str] = {}
        self._generic_agent_identities: dict[str, dict[str, object]] = {}
        self._closed = False

    @property
    def active(self) -> bool:
        """Report whether a current run is accepting observation events."""
        with self._lock:
            return not self._closed and self._run is not None

    def begin_run(self, request: object) -> str:
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

    def end_run(self, result: object = None, *, error: BaseException | None = None) -> None:
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

        object card still marked running is converted to a stopped warning. That
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
        input_value: object | None = None,
        attributes: Mapping[str, object] | None = None,
    ) -> "LiveSpan | None":
        """Open one semantic event or an invisible aggregation context.

        Agent and helper spans provide ownership only. An LLM span under an
        Agent becomes one Agent-turn card and a real tool span becomes a Tool
        card. Every other span stays invisible while forwarding semantic scope.
        """
        from .span import LiveSpan

        try:
            if not self.active:
                return None
            parent = _CURRENT_CONTEXT.get()
            safe_attributes = self._safe(dict(attributes or {}))
            scope = _merge_scope(parent.scope, safe_attributes)
            context_id = f"context_{uuid4().hex}"
            event: dict[str, object] | None = None
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
        request_kwargs: Mapping[str, object] | None,
        operation_key: str | None,
        path_template: str | None,
    ) -> "LiveHTTPExchange | None":
        """Attach one final prepared target request to its semantic owner.

        A request under ``restscope.http.request`` enriches that Tool card.
        Requests inside another Tool remain represented by that Tool's bounded
        result instead of becoming duplicate timeline events.
        """
        from .http_exchange import LiveHTTPExchange

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
                )
            return None
        except Exception:
            return None

    def snapshot(self) -> dict[str, object]:
        """Return one atomic schema-v3 browser snapshot of the current run."""
        with self._lock:
            return {
                "schema_version": 3,
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
    ) -> list[dict[str, object]]:
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
        scope: Mapping[str, object],
        context_id: str,
    ) -> dict[str, object]:
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
        attributes: Mapping[str, object],
        scope: dict[str, object],
        input_value: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Create one explicit generic Agent identity and task scope.

        Generic Main, Subagent, and System sessions supply their Harness-owned
        IDs in generic Agent lifecycle attributes. Unlike legacy ``kind=AGENT``
        spans, these identities are authoritative enough for the UI to select a
        Main conversation and navigate its children without guessing names.
        """
        session_id = str(attributes.get("restscope.agent.session_id") or "")
        profile_name = str(attributes.get("restscope.agent.profile") or "")
        lifecycle = str(attributes.get("restscope.agent.lifecycle") or "")
        if not session_id or not profile_name or lifecycle not in {
            "main",
            "subagent",
            "system",
        }:
            return parent.agent or {}, scope

        parent_session_value = attributes.get("restscope.agent.parent_session_id")
        parent_session_id = (
            str(parent_session_value) if parent_session_value is not None else None
        )
        parent_identity = self._generic_agent_identities.get(parent_session_id or "")
        parent_path = parent_identity.get("path", []) if parent_identity else []
        task_id = f"task_{uuid4().hex}"
        identity: dict[str, object] = {
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

    def _complete_agent_task(self, *, task_id: str, output: object) -> None:
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
        agent: dict[str, object] | None,
        scope: dict[str, object],
        input_value: object,
        attributes: Mapping[str, object],
    ) -> dict[str, object]:
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

    def _upsert(self, event: dict[str, object]) -> None:
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

    def _update_event(self, event_id: str, **changes: object) -> dict[str, object] | None:
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

    def _event_copy(self, event_id: str | None) -> dict[str, object] | None:
        """Read one event safely for a later copy-on-write update."""
        if event_id is None:
            return None
        with self._lock:
            event = self._events.get(event_id)
            return deepcopy(event) if event is not None else None

    def _publish_locked(self, event_type: str, data: dict[str, object]) -> None:
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

    def _safe(self, value: object) -> object:
        """Convert a value to JSON data using the App's exact-value policy."""
        return self._redactor.redact(value)

    def _set_agent_messages(
        self,
        *,
        event_id: str,
        direction: str,
        messages: list[dict[str, object]],
        summary: object,
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
                new_messages: list[dict[str, object]] = []
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
                exact_tool_calls: list[object] = []
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

    def _set_event_detail_value(self, event_id: str, name: str, value: object) -> None:
        """Add observer-only detail without changing exported Phoenix fields."""
        event = self._event_copy(event_id)
        if event is None:
            return
        detail = deepcopy(event.get("detail", {}))
        detail[name] = self._safe(value)
        self._update_event(event_id, detail=detail)

    def _record_todo(self, tool_event: dict[str, object]) -> None:
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
