import pytest

from schemathesis_mcp.server import create_server
from schemathesis_mcp.tools import ToolService


class StubBackend:
    def __init__(self):
        self.probed = False

    def probe(self):
        self.probed = True
        return {"version": "4.99.0"}

    def configure_run(self, run_id, run_dir):
        pass

    def execute(self, run_id, request, stop_event):
        yield {
            "type": "run_finished",
            "timestamp": 1.0,
            "outcome": "passed",
            "stop_reason": "completed",
            "exit_code": 0,
            "cli_version": "4.99.0",
            "command": "schemathesis run [REDACTED]",
            "schema": {"kind": "inline", "sha256": "abc"},
        }

    def terminate(self, run_id):
        pass


def test_tool_service_exposes_cli_run_lifecycle(tmp_path) -> None:
    backend = StubBackend()
    service = ToolService.create(backend=backend, artifact_root=tmp_path)
    assert backend.probed is True
    started = service.start_run(schema={"kind": "inline", "format": "yaml", "content": "openapi: 3.0.0"})
    service.runs.wait(started["run_id"], timeout=1)

    assert service.get_run(started["run_id"])["state"] == "completed"
    result = service.get_result(started["run_id"])
    assert result["outcome"] == "passed"
    assert result["cli_version"] == "4.99.0"


@pytest.mark.asyncio
async def test_server_registers_only_cli_first_tools(tmp_path) -> None:
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)
    server = create_server(service)

    names = {tool.name for tool in server._tool_manager.list_tools()}

    assert names == {
        "start_run",
        "get_run",
        "get_events",
        "get_result",
        "get_failure",
        "cancel_run",
    }
