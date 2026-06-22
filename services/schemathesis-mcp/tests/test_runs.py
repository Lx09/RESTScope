import threading
import time
from types import SimpleNamespace

import pytest

from schemathesis_mcp.artifacts import ArtifactStore
from schemathesis_mcp.models import RunOutcome, RunState
from schemathesis_mcp.runs import RunCapacityExceeded, RunManager


class FakeBackend:
    def execute(self, run_id, request, stop_event: threading.Event):
        yield {"type": "engine_started", "timestamp": 1.0}
        while not stop_event.is_set():
            time.sleep(0.005)
        yield {
            "type": "engine_finished",
            "timestamp": 2.0,
            "outcome": "interrupted",
            "stop_reason": "interrupted",
        }


def test_run_manager_starts_tracks_and_cancels_run(tmp_path) -> None:
    manager = RunManager(backend=FakeBackend(), artifacts=ArtifactStore(tmp_path))
    run_id = manager.start({"schema": "api.yaml"})

    deadline = time.monotonic() + 1
    while manager.get(run_id).state is RunState.QUEUED and time.monotonic() < deadline:
        time.sleep(0.005)

    assert manager.get(run_id).state is RunState.RUNNING
    assert manager.cancel(run_id).state is RunState.CANCELLING
    manager.wait(run_id, timeout=1)

    status = manager.get(run_id)
    assert status.state is RunState.CANCELLED
    assert status.outcome is RunOutcome.INTERRUPTED


class FailingBackend:
    def execute(self, run_id, request, stop_event):
        raise RuntimeError("backend exploded")
        yield


def test_backend_errors_are_available_as_completed_result(tmp_path) -> None:
    manager = RunManager(backend=FailingBackend(), artifacts=ArtifactStore(tmp_path))
    run_id = manager.start({"schema": "api.yaml"})
    manager.wait(run_id, timeout=1)

    assert manager.get(run_id).state is RunState.FAILED
    result = manager.get_result(run_id)
    assert result.outcome is RunOutcome.ERRORED
    assert result.summary["error"] == "backend exploded"


def test_max_time_requests_cancellation(tmp_path) -> None:
    manager = RunManager(backend=FakeBackend(), artifacts=ArtifactStore(tmp_path))
    run_id = manager.start(SimpleNamespace(max_time=0.02))
    manager.wait(run_id, timeout=1)

    assert manager.get(run_id).state is RunState.CANCELLED


def test_concurrency_limit_rejects_new_run(tmp_path) -> None:
    manager = RunManager(backend=FakeBackend(), artifacts=ArtifactStore(tmp_path), max_concurrent=1)
    first = manager.start({"schema": "api.yaml"})

    with pytest.raises(RunCapacityExceeded):
        manager.start({"schema": "api.yaml"})

    manager.cancel(first)
    manager.wait(first, timeout=1)


def test_cleanup_marks_completed_runs_expired_and_removes_artifacts(tmp_path) -> None:
    manager = RunManager(
        backend=FailingBackend(),
        artifacts=ArtifactStore(tmp_path),
        artifact_ttl_seconds=0,
    )
    run_id = manager.start({"schema": "api.yaml"})
    manager.wait(run_id, timeout=1)

    manager.cleanup_expired()

    assert manager.get(run_id).state is RunState.EXPIRED
    assert not (tmp_path / run_id).exists()
