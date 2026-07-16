"""Run lifecycle manager for asynchronous Schemathesis executions."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from schemathesis_mcp.artifacts import ArtifactStore
from schemathesis_mcp.models import RunOutcome, RunProgress, RunResult, RunState, RunStatus


class Backend(Protocol):
    def execute(self, run_id: str, request: Any, stop_event: threading.Event) -> Iterable[dict[str, Any]]: ...


class RunCapacityExceeded(RuntimeError):
    pass


class RunManager:
    def __init__(
        self,
        backend: Backend,
        artifacts: ArtifactStore,
        max_concurrent: int = 4,
        artifact_ttl_seconds: float = 3_600,
    ) -> None:
        self.backend = backend
        self.artifacts = artifacts
        self.max_concurrent = max_concurrent
        self.artifact_ttl_seconds = artifact_ttl_seconds
        self._statuses: dict[str, RunStatus] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._run_metadata: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def start(self, request: Any) -> str:
        self.cleanup_expired()
        run_id = uuid.uuid4().hex
        stop_event = threading.Event()
        with self._lock:
            active = sum(
                status.state in {RunState.QUEUED, RunState.LOADING, RunState.RUNNING, RunState.CANCELLING}
                for status in self._statuses.values()
            )
            if active >= self.max_concurrent:
                raise RunCapacityExceeded(f"At most {self.max_concurrent} runs may execute concurrently")
            self.artifacts.create_run(run_id)
            self._statuses[run_id] = RunStatus(run_id=run_id)
            self._stop_events[run_id] = stop_event
            thread = threading.Thread(
                target=self._execute,
                args=(run_id, request, stop_event),
                name=f"schemathesis-mcp-{run_id[:8]}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
        return run_id

    def get(self, run_id: str) -> RunStatus:
        with self._lock:
            return self._statuses[run_id].model_copy(deep=True)

    def cancel(self, run_id: str) -> RunStatus:
        should_terminate = False
        with self._lock:
            status = self._statuses[run_id]
            if status.state in {RunState.QUEUED, RunState.LOADING, RunState.RUNNING}:
                status.state = RunState.CANCELLING
                self._stop_events[run_id].set()
                should_terminate = True
            result = status.model_copy(deep=True)
        if should_terminate:
            terminate = getattr(self.backend, "terminate", None)
            if terminate is not None:
                terminate(run_id)
        return result

    def wait(self, run_id: str, timeout: float | None = None) -> None:
        self._threads[run_id].join(timeout)

    def get_result(self, run_id: str) -> RunResult:
        return self.artifacts.read_result(run_id)

    def cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        with self._lock:
            for run_id, status in self._statuses.items():
                if (
                    status.finished_at is not None
                    and status.state in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
                    and (now - status.finished_at).total_seconds() >= self.artifact_ttl_seconds
                ):
                    self.artifacts.expire_run(run_id)
                    status.state = RunState.EXPIRED

    def _execute(self, run_id: str, request: Any, stop_event: threading.Event) -> None:
        timer: threading.Timer | None = None
        with self._lock:
            status = self._statuses[run_id]
            if status.state is not RunState.CANCELLING:
                status.state = RunState.RUNNING
            status.started_at = datetime.now(UTC)
        max_time = getattr(request, "max_time", None)
        if max_time is not None:
            timer = threading.Timer(max_time, lambda: self.cancel(run_id))
            timer.daemon = True
            timer.start()
        try:
            configure_run = getattr(self.backend, "configure_run", None)
            if configure_run is not None:
                configure_run(run_id, self.artifacts.run_dir(run_id))
            failure_ids: list[str] = []
            for event in self.backend.execute(run_id, request, stop_event):
                public_event = deepcopy(event)
                for failure_payload in public_event.pop("_failures", []):
                    from schemathesis_mcp.models import FailureDetail

                    failure = FailureDetail.model_validate(failure_payload)
                    self.artifacts.write_failure(run_id, failure)
                    if failure.failure_id not in failure_ids:
                        failure_ids.append(failure.failure_id)
                self.artifacts.append_event(run_id, public_event)
                self._on_event(run_id, public_event)
            with self._lock:
                status = self._statuses[run_id]
                cancelled = stop_event.is_set() or status.outcome is RunOutcome.INTERRUPTED
                status.state = (
                    RunState.CANCELLED
                    if cancelled
                    else RunState.FAILED
                    if status.outcome is RunOutcome.ERRORED
                    else RunState.COMPLETED
                )
                status.finished_at = datetime.now(UTC)
                if status.outcome is None:
                    status.outcome = RunOutcome.FAILED if status.progress.failures else RunOutcome.PASSED
                metadata = self._run_metadata.get(run_id, {})
                result = RunResult(
                    run_id=run_id,
                    outcome=status.outcome,
                    stop_reason=status.stop_reason,
                    summary=status.progress.model_dump(),
                    failure_ids=failure_ids,
                    artifacts=self.artifacts.artifact_uris(run_id),
                    cli_version=metadata.get("cli_version"),
                    command=metadata.get("command"),
                    exit_code=metadata.get("exit_code"),
                    schema=metadata.get("schema"),
                )
            self.artifacts.write_result(result)
        except Exception as exc:
            with self._lock:
                status = self._statuses[run_id]
                status.state = RunState.FAILED
                status.outcome = RunOutcome.ERRORED
                status.error = str(exc)
                status.finished_at = datetime.now(UTC)
                result = RunResult(
                    run_id=run_id,
                    outcome=RunOutcome.ERRORED,
                    summary={"error": str(exc), **status.progress.model_dump()},
                    artifacts=self.artifacts.artifact_uris(run_id),
                )
            self.artifacts.write_result(result)
        finally:
            if timer is not None:
                timer.cancel()

    def _on_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            status = self._statuses[run_id]
            progress = status.progress.model_copy()
            progress.events += 1
            event_type = event.get("type")
            if event_type == "loading_started":
                status.state = RunState.LOADING
            elif event_type == "loading_finished":
                status.state = RunState.CANCELLING if self._stop_events[run_id].is_set() else RunState.RUNNING
            elif event_type == "phase_started":
                status.current_phase = event.get("phase")
            elif event_type == "scenario_finished":
                progress.scenarios += 1
                progress.failures += int(event.get("failures", 0))
            elif event_type in {"non_fatal_error", "fatal_error"}:
                progress.errors += 1
            elif event_type == "engine_finished":
                status.stop_reason = event.get("stop_reason")
                raw_outcome = event.get("outcome")
                if raw_outcome is not None:
                    status.outcome = RunOutcome(raw_outcome)
                elif event.get("after_run_failures", 0):
                    progress.failures += int(event["after_run_failures"])
            elif event_type == "run_started":
                status.state = RunState.RUNNING
            elif event_type == "run_finished":
                status.stop_reason = event.get("stop_reason")
                status.outcome = RunOutcome(event["outcome"])
                self._run_metadata[run_id] = event
            status.progress = RunProgress.model_validate(progress)
