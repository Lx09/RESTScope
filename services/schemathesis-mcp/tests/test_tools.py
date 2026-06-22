import pytest

from schemathesis_mcp.server import create_server
from schemathesis_mcp.tools import ToolService


class StubBackend:
    def inspect(self, request):
        return {"schema": request.schema_location, "operations": []}

    def execute(self, run_id, request, stop_event):
        yield {
            "type": "engine_finished",
            "timestamp": 1.0,
            "outcome": "passed",
            "stop_reason": "completed",
        }

    def replay(self, run_id, failure_id):
        return {"reproduced": True, "status_code": 500}


def test_tool_service_exposes_run_lifecycle(tmp_path) -> None:
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)

    inspected = service.inspect_api(schema="api.yaml")
    started = service.start_run(schema="api.yaml")
    service.runs.wait(started["run_id"], timeout=1)

    assert inspected["schema"] == "api.yaml"
    assert service.get_run(started["run_id"])["state"] == "completed"
    assert service.get_result(started["run_id"])["outcome"] == "passed"
    assert service.replay_failure(started["run_id"], "failure-1")["reproduced"] is True


@pytest.mark.asyncio
async def test_server_registers_expected_tools_and_resource_template(tmp_path) -> None:
    service = ToolService.create(backend=StubBackend(), artifact_root=tmp_path)
    server = create_server(service)

    names = {tool.name for tool in server._tool_manager.list_tools()}
    templates = await server.list_resource_templates()

    assert names == {
        "inspect_api",
        "start_run",
        "get_run",
        "get_events",
        "get_result",
        "get_failure",
        "cancel_run",
        "replay_failure",
    }
    assert any(str(template.uriTemplate).startswith("schemathesis://runs/") for template in templates)
