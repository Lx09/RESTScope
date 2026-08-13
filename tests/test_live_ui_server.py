"""Read-only HTTP and SSE contracts for the optional live observer service."""

from __future__ import annotations

import asyncio
from pathlib import Path


def _static_fixture(tmp_path: Path) -> Path:
    """Create the minimum built-asset shape required by the server adapter."""
    static_root = tmp_path / "static"
    assets = static_root / "assets"
    assets.mkdir(parents=True)
    (static_root / "index.html").write_text(
        "<!doctype html><title>RESTScope observer</title>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('observer')", encoding="utf-8")
    return static_root


class _SnapshotObserver:
    """Supply deterministic snapshot data without starting a RESTScope run."""

    def snapshot(self) -> dict[str, object]:
        """Return one empty current-run snapshot."""
        return {
            "schema_version": 2,
            "run": {"run_id": "run-1", "status": "running"},
            "events": [],
            "latest_cursor": 7,
            "todo": None,
        }


def test_ui_routes_are_get_only_and_apply_no_store_security_headers(
    tmp_path: Path,
) -> None:
    """Scenario: viewers can read state but cannot post commands or cache secrets."""
    from starlette.testclient import TestClient

    from restscope.ui.server import build_ui_app

    app = build_ui_app(_SnapshotObserver(), static_root=_static_fixture(tmp_path))

    with TestClient(app) as client:
        snapshot = client.get("/api/v1/run")
        index = client.get("/")
        rejected_write = client.post("/api/v1/run", json={"action": "pause"})

    assert snapshot.status_code == 200
    assert snapshot.json()["latest_cursor"] == 7
    assert snapshot.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in snapshot.headers["content-security-policy"]
    assert snapshot.headers["x-content-type-options"] == "nosniff"
    assert index.status_code == 200
    assert rejected_write.status_code == 405


class _StreamObserver:
    """Record the reconnect cursor and return one predictable stream change."""

    def __init__(self, changes: list[dict[str, object]]) -> None:
        """Store the changes the next cursor wait should reveal."""
        self.changes = changes
        self.calls: list[tuple[int, float]] = []

    def wait_after(self, cursor: int, timeout_seconds: float) -> list[dict[str, object]]:
        """Return configured changes and record the adapter's wait request."""
        self.calls.append((cursor, timeout_seconds))
        return self.changes


def test_sse_uses_newest_reconnect_cursor_and_serializes_named_incremental_events(
    tmp_path: Path,
) -> None:
    """Scenario: a reconnect receives ordered named events after its cursor."""
    from starlette.requests import Request

    from restscope.ui.server import build_ui_app

    observer = _StreamObserver(
        [
            {
                "cursor": 10,
                "type": "timeline.upsert",
                "data": {"event": {"event_id": "event-1", "kind": "agent_turn"}},
            }
        ]
    )
    app = build_ui_app(observer, static_root=_static_fixture(tmp_path))
    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/v1/events"
    )

    async def first_chunk() -> str:
        """Open one request and read only the first emitted SSE frame."""
        received = False

        async def receive() -> dict[str, object]:
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/events",
                "query_string": b"after=8",
                "headers": [(b"last-event-id", b"9")],
            },
            receive,
        )
        response = await endpoint(request)
        return await anext(response.body_iterator)

    chunk = asyncio.run(first_chunk())

    assert observer.calls == [(9, 15.0)]
    assert chunk.startswith("id: 10\nevent: timeline.upsert\n")
    assert '"event_id":"event-1"' in chunk


def test_sse_emits_a_heartbeat_when_no_changes_arrive(tmp_path: Path) -> None:
    """Scenario: an idle connection remains alive without inventing an event."""
    from starlette.requests import Request

    from restscope.ui.server import build_ui_app

    observer = _StreamObserver([])
    app = build_ui_app(observer, static_root=_static_fixture(tmp_path))
    endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/v1/events"
    )

    async def first_chunk() -> str:
        """Read the first idle frame emitted by the stream."""
        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/events",
                "query_string": b"",
                "headers": [],
            },
            receive,
        )
        response = await endpoint(request)
        return await anext(response.body_iterator)

    assert asyncio.run(first_chunk()) == ": heartbeat\n\n"
    assert observer.calls == [(0, 15.0)]
