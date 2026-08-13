"""Own one Main Agent's in-memory tree, slots, cancellation, and waiting.

The control object is created with the Main Agent and shared by every child.
It is the deterministic authority for parentage and lifecycle state; models see
only bounded projections through the three global ``subagent.*`` Tools.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
from threading import Condition, Event, RLock, Semaphore
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from restscope.agent import AgentError, AgentResult, AgentTask
from restscope.tools import ToolFailure

if TYPE_CHECKING:
    from restscope.agent import Agent


_TreeStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


@dataclass
class _AgentRecord:
    """Keep private state for one open or previously collected Agent."""

    session_id: str
    profile_name: str
    parent_id: str | None
    depth: int
    status: _TreeStatus
    cancel_event: Event = field(default_factory=Event)
    result: AgentResult | None = None
    collected: bool = False
    version: int = 0


BuildChild = Callable[[str, "AgentTreeControl", int, str, str, Event], "Agent"]


@dataclass(frozen=True)
class BudgetCharge:
    """Describe one atomic model-response charge and newly crossed reminders."""

    weighted_tokens: float
    remaining_tokens: float
    exceeded: bool
    reminder_percentages: tuple[int, ...]


class RolloutBudget:
    """Share weighted model usage across one root Agent and all descendants.

    ``None`` keeps accounting active without enforcing a ceiling. System Agent
    roots use that mode so Harness validation can continue until a valid result
    or an external terminal condition occurs.
    """

    _REMINDER_PERCENTAGES = (50, 25, 10)

    def __init__(self, limit: float | None = 1_000_000) -> None:
        """Create an atomic App-memory allowance with one-shot reminders."""
        if limit is not None and limit <= 0:
            raise ValueError("rollout budget must be greater than zero")
        self.limit = float(limit) if limit is not None else None
        self._used = 0.0
        self._sent: set[int] = set()
        self._lock = RLock()

    def charge(
        self,
        *,
        prompt_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> BudgetCharge:
        """Charge output fully and only non-cached input at one tenth weight."""
        non_cached_input = max(0, prompt_tokens - cached_input_tokens)
        weighted = output_tokens + non_cached_input * 0.1
        with self._lock:
            if self.limit is None:
                self._used += weighted
                return BudgetCharge(
                    weighted_tokens=weighted,
                    remaining_tokens=0.0,
                    exceeded=False,
                    reminder_percentages=(),
                )
            before_remaining = self.limit - self._used
            self._used += weighted
            remaining = self.limit - self._used
            crossed = tuple(
                percentage
                for percentage in self._REMINDER_PERCENTAGES
                if percentage not in self._sent
                and before_remaining > self.limit * percentage / 100 >= remaining
            )
            self._sent.update(crossed)
            return BudgetCharge(
                weighted_tokens=weighted,
                remaining_tokens=max(0.0, remaining),
                exceeded=remaining < 0,
                reminder_percentages=crossed,
            )

    @property
    def used_tokens(self) -> float:
        """Return the current weighted use for results and diagnostics."""
        with self._lock:
            return self._used


class AgentExecutionLimiter:
    """Limit simultaneous provider/Tool work without counting wait calls."""

    def __init__(self, maximum: int) -> None:
        """Create one tree-wide permit pool."""
        if maximum < 1:
            raise ValueError("max_active_agents must be greater than zero")
        self._semaphore = Semaphore(maximum)

    def call(self, action: Callable, /, *args, **kwargs):
        """Run one bounded provider or ordinary Tool call under a permit."""
        with self._semaphore:
            return action(*args, **kwargs)


class AgentTreeControl:
    """Coordinate direct-child access and all App-lifetime tree state."""

    def __init__(
        self,
        *,
        build_child: BuildChild,
        max_open_agents: int = 4,
        max_active_agents: int = 4,
        rollout_budget_weighted_tokens: float | None = 1_000_000,
    ) -> None:
        """Create an empty tree before its Main Agent is registered."""
        if max_open_agents < 1:
            raise ValueError("max_open_agents must be greater than zero")
        self._build_child = build_child
        self._max_open_agents = max_open_agents
        self._limiter = AgentExecutionLimiter(max_active_agents)
        self.budget = RolloutBudget(rollout_budget_weighted_tokens)
        self._lock = RLock()
        self._changed = Condition(self._lock)
        # More worker threads than active permits lets waiting children release
        # execution capacity without occupying the entire submission pool.
        self._executor = ThreadPoolExecutor(max_workers=max(8, max_open_agents * 2))
        self._records: dict[str, _AgentRecord] = {}
        self._open_count = 0

    def register_root(
        self,
        session_id: str,
        profile_name: str,
        cancel_event: Event | None = None,
    ) -> None:
        """Reserve the tree's first open slot for its Main or System root."""
        with self._lock:
            if self._records:
                raise RuntimeError("Agent tree already has a root Agent")
            self._records[session_id] = _AgentRecord(
                session_id=session_id,
                profile_name=profile_name,
                parent_id=None,
                depth=0,
                status="running",
                cancel_event=cancel_event or Event(),
            )
            self._open_count = 1

    def invoke_model(self, action: Callable, /, *args, **kwargs):
        """Count one active provider call against the shared limiter."""
        return self._limiter.call(action, *args, **kwargs)

    def charge_response(self, response) -> BudgetCharge:
        """Atomically account for one provider or compaction response."""
        return self.budget.charge(
            prompt_tokens=response.prompt_tokens or 0,
            cached_input_tokens=response.cached_input_tokens,
            output_tokens=response.completion_tokens or 0,
        )

    def execute_tool(self, name: str, action: Callable, /, *args, **kwargs):
        """Run ordinary Tools under a permit while wait remains non-blocking."""
        if name == "subagent.wait":
            return action(*args, **kwargs)
        return self._limiter.call(action, *args, **kwargs)

    def start_child(
        self,
        *,
        owner_id: str,
        allowed_profile_names: tuple[str, ...],
        profile_name: str,
        objective: str,
    ) -> dict:
        """Atomically reserve, submit, and identify one authorized child."""
        with self._lock:
            owner = self._direct_owner(owner_id)
            if profile_name not in allowed_profile_names:
                raise ToolFailure(
                    code="subagent_profile_not_authorized",
                    message="The requested Subagent Profile is not authorized for this Agent.",
                )
            depth = owner.depth + 1
            if depth > 3:
                raise ToolFailure(
                    code="subagent_depth_exceeded",
                    message="The Subagent depth limit has been reached.",
                )
            if self._open_count >= self._max_open_agents:
                raise ToolFailure(
                    code="subagent_capacity_exceeded",
                    message="The Agent tree has no open Subagent slot.",
                )
            session_id = f"agent_{uuid4().hex}"
            record = _AgentRecord(
                session_id=session_id,
                profile_name=profile_name,
                parent_id=owner_id,
                depth=depth,
                status="queued",
            )
            self._records[session_id] = record
            self._open_count += 1
            context = copy_context()
            try:
                self._executor.submit(
                    context.run,
                    self._run_child,
                    record,
                    objective,
                )
            except Exception as exc:
                # Submission owns the reservation transaction. Roll it back
                # before exposing a stable expected failure to the parent.
                del self._records[session_id]
                self._open_count -= 1
                raise ToolFailure(
                    code="subagent_submission_failed",
                    message="The Subagent could not be submitted.",
                ) from exc
            return {
                "subagent_id": session_id,
                "profile_name": profile_name,
                "status": "queued",
                "depth": depth,
            }

    def wait_children(
        self,
        *,
        owner_id: str,
        subagent_ids: tuple[str, ...],
        timeout_seconds: int,
    ) -> dict:
        """Wait for a terminal child or timeout, then collect terminal results."""
        deadline = time.monotonic() + timeout_seconds
        with self._changed:
            records = [self._require_direct_child(owner_id, value) for value in subagent_ids]
            initial_versions = {
                record.session_id: record.version for record in records
            }
            while not any(
                record.status in {"completed", "failed", "cancelled"}
                or record.version != initial_versions[record.session_id]
                for record in records
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(timeout=remaining)
            timed_out = not any(
                record.status in {"completed", "failed", "cancelled"}
                or record.version != initial_versions[record.session_id]
                for record in records
            )
            agents = [self._snapshot(record) for record in records]
            for record in records:
                if (
                    record.status in {"completed", "failed", "cancelled"}
                    and not record.collected
                ):
                    record.collected = True
                    self._open_count -= 1
            return {"timed_out": timed_out, "agents": agents}

    def cancel_child(
        self,
        *,
        owner_id: str,
        subagent_id: str,
        reason: str | None,
    ) -> dict:
        """Set a direct child's cooperative cancellation flag."""
        del reason  # Bounded reason is intentionally not retained as tree state.
        with self._lock:
            record = self._require_direct_child(owner_id, subagent_id)
            if record.status in {"completed", "failed", "cancelled"}:
                return {"subagent_id": subagent_id, "status": "already_terminal"}
            record.cancel_event.set()
            return {
                "subagent_id": subagent_id,
                "status": "cancellation_requested",
            }

    def close_descendants(self, owner_id: str) -> None:
        """Cooperatively cancel every uncollected descendant of an owner."""
        with self._lock:
            descendants = self._descendant_ids(owner_id)
            for session_id in descendants:
                self._records[session_id].cancel_event.set()

    def close(self) -> None:
        """Cancel the complete tree and stop accepting executor submissions."""
        with self._lock:
            for record in self._records.values():
                if not record.collected:
                    record.cancel_event.set()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_child(self, record: _AgentRecord, objective: str) -> None:
        """Construct and run one child, converting escaped exceptions safely."""
        with self._changed:
            record.status = "running"
            record.version += 1
            self._changed.notify_all()
        agent: Agent | None = None
        try:
            agent = self._build_child(
                record.profile_name,
                self,
                record.depth,
                record.parent_id or "",
                record.session_id,
                record.cancel_event,
            )
            result = agent.run(AgentTask(objective=objective))
        except Exception:  # noqa: BLE001
            result = AgentResult(
                session_id=record.session_id,
                profile_name=record.profile_name,
                status="failed",
                error=AgentError(
                    code="subagent_execution_failed",
                    message="The Subagent failed because of an internal runtime error.",
                ),
            )
        finally:
            if agent is not None:
                agent.close()
        with self._changed:
            record.result = result
            if result.status == "completed":
                record.status = "completed"
            elif result.status == "cancelled":
                record.status = "cancelled"
            else:
                record.status = "failed"
            record.version += 1
            self._changed.notify_all()

    def _direct_owner(self, owner_id: str) -> _AgentRecord:
        """Return one live tree member that may own child Tools."""
        try:
            return self._records[owner_id]
        except KeyError as exc:
            raise ToolFailure(
                code="subagent_owner_closed",
                message="The owning Agent is no longer open.",
            ) from exc

    def _require_direct_child(self, owner_id: str, child_id: str) -> _AgentRecord:
        """Reject siblings, ancestors, foreign IDs, and collected children alike."""
        record = self._records.get(child_id)
        if record is None or record.parent_id != owner_id or record.collected:
            raise ToolFailure(
                code="subagent_not_direct_child",
                message="The requested ID is not an open direct child of this Agent.",
            )
        return record

    @staticmethod
    def _snapshot(record: _AgentRecord) -> dict:
        """Project private result state into the fixed Tool output contract."""
        output = {
            "subagent_id": record.session_id,
            "profile_name": record.profile_name,
            "status": record.status,
            "completion": None,
            "error": None,
        }
        if record.result is not None:
            output["completion"] = (
                record.result.completion.model_dump(mode="json")
                if record.result.completion is not None
                else None
            )
            output["error"] = (
                record.result.error.model_dump(mode="json")
                if record.result.error is not None
                else None
            )
        return output

    def _descendant_ids(self, owner_id: str) -> set[str]:
        """Find descendants without exposing tree topology to a model."""
        found: set[str] = set()
        frontier = [owner_id]
        while frontier:
            parent = frontier.pop()
            children = [
                record.session_id
                for record in self._records.values()
                if record.parent_id == parent and not record.collected
            ]
            found.update(children)
            frontier.extend(children)
        return found
