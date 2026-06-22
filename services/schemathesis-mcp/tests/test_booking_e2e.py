from __future__ import annotations

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from schemathesis_mcp.adapter import SchemathesisBackend
from schemathesis_mcp.tools import ToolService

BOOKING_APP = Path("/Users/lixin/Workplace/schemathesis-master/examples/booking/app.py")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not BOOKING_APP.exists(), reason="Schemathesis booking example is unavailable")
def test_booking_example_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    spec = importlib.util.spec_from_file_location("schemathesis_booking_app", BOOKING_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        service = ToolService.create(backend=SchemathesisBackend(), artifact_root=tmp_path)
        started = service.start_run(
            schema=f"http://127.0.0.1:{port}/openapi.json",
            headers={"Authorization": "Bearer secret-token"},
            phases=["examples", "coverage", "fuzzing", "stateful"],
            generation_modes=["positive", "negative"],
            include={"path": "/bookings", "method": "POST"},
            max_examples=2,
            max_failures=1,
            seed=1,
        )
        service.runs.wait(started["run_id"], timeout=30)

        result = service.get_result(started["run_id"])
        events = service.get_events(started["run_id"], limit=500)["events"]

        assert result["outcome"] == "failed"
        assert result["failure_ids"]
        assert any(event["payload"].get("phase") == "coverage" for event in events)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
