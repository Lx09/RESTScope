import json

import pytest

from schemathesis_mcp.artifacts import ArtifactStore, CursorExpired


def test_event_pages_use_monotonic_cursor_and_write_ndjson(tmp_path) -> None:
    store = ArtifactStore(tmp_path, max_events=3)
    store.create_run("run-1")

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
