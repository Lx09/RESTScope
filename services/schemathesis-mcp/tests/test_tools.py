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


def test_tool_service_exposes_safe_capability_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCHEMATHESIS_CLI", "/private/bin/schemathesis")
    monkeypatch.setenv("SCHEMATHESIS_MCP_ALLOWED_PATHS", "/secret/contracts")
    monkeypatch.setenv("SCHEMATHESIS_MCP_ALLOWED_HOSTS", "api.example.com")
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)

    capabilities = service.get_capabilities()

    assert capabilities["name"] == "schemathesis-mcp"
    assert capabilities["version"] == "0.1.0"
    assert capabilities["transport"] == "stdio"
    assert capabilities["backend"] == {
        "type": "schemathesis-cli",
        "cli_version": "4.99.0",
        "command_overridden": True,
    }
    assert capabilities["tools"] == [
        "get_capabilities",
        "start_run",
        "get_run",
        "get_events",
        "get_result",
        "get_failure",
        "cancel_run",
    ]
    assert capabilities["schema_inputs"] == {
        "kinds": ["file", "url", "inline"],
        "inline_formats": ["yaml", "json"],
    }
    assert capabilities["run_options"]["reports"] == ["junit", "har", "vcr", "allure"]
    assert capabilities["limits"] == {
        "max_concurrent_runs": 4,
        "artifact_ttl_seconds": 3600,
    }
    assert capabilities["configuration"]["path_policy"] == {
        "default_allows_current_working_directory": True,
        "additional_roots_configured": True,
    }
    assert capabilities["configuration"]["target_policy"] == {
        "host_allowlist_configured": True,
    }
    assert all(entry["configured"] is True for entry in capabilities["configuration"]["env"])
    assert "/private/bin/schemathesis" not in str(capabilities)
    assert "/secret/contracts" not in str(capabilities)
    assert "api.example.com" not in str(capabilities)


@pytest.mark.asyncio
async def test_server_registers_only_cli_first_tools(tmp_path) -> None:
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)
    server = create_server(service)

    names = {tool.name for tool in server._tool_manager.list_tools()}

    assert names == {
        "get_capabilities",
        "start_run",
        "get_run",
        "get_events",
        "get_result",
        "get_failure",
        "cancel_run",
    }


@pytest.mark.asyncio
async def test_server_registers_tool_annotations(tmp_path) -> None:
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)
    server = create_server(service)

    annotations = {
        tool.name: tool.annotations.model_dump(exclude_none=True)
        for tool in server._tool_manager.list_tools()
    }

    assert annotations == {
        "get_capabilities": {
            "title": "Get capabilities",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "start_run": {
            "title": "Start run",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "get_run": {
            "title": "Get run",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "get_events": {
            "title": "Get events",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "get_result": {
            "title": "Get result",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "get_failure": {
            "title": "Get failure",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "cancel_run": {
            "title": "Cancel run",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }
