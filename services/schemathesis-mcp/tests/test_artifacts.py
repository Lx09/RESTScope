"""Tests for artifact storage, event pagination, and resource safety."""

import json
import stat

import pytest

from schemathesis_mcp.artifacts import ArtifactStore, CursorExpired
from schemathesis_mcp.models import FailureDetail


def test_event_pages_use_monotonic_cursor_and_write_ndjson(tmp_path) -> None:
    store = ArtifactStore(tmp_path, max_events=3)
    store.create_run("run-1")

    assert stat.S_IMODE((tmp_path / "run-1").stat().st_mode) == 0o700

    for index in range(4):
        store.append_event("run-1", {"index": index})

    page = store.get_events("run-1", cursor=1, limit=2)
    assert [entry.payload for entry in page.events] == [{"index": 1}, {"index": 2}]
    assert page.next_cursor == 3

    lines = (tmp_path / "run-1" / "events.ndjson").read_text().splitlines()
    assert [json.loads(line)["payload"] for line in lines] == [
        {"index": 0},
        {"index": 1},
        {"index": 2},
        {"index": 3},
    ]


def test_old_cursor_expires_when_memory_buffer_rolls_over(tmp_path) -> None:
    store = ArtifactStore(tmp_path, max_events=2)
    store.create_run("run-1")
    for index in range(3):
        store.append_event("run-1", {"index": index})

    with pytest.raises(CursorExpired) as exc_info:
        store.get_events("run-1", cursor=0, limit=10)

    assert exc_info.value.artifact_uri == "schemathesis://runs/run-1/events.ndjson"


def test_duplicate_failure_ids_are_aggregated(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    store.create_run("run-1")
    failure = FailureDetail(
        failure_id="same",
        operation="GET /users",
        check="not_a_server_error",
        title="ServerError",
        message="Server error",
    )

    store.write_failure("run-1", failure)
    store.write_failure("run-1", failure)

    assert store.read_failure("run-1", "same").count == 2
