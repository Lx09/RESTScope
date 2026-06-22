from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from schemathesis_mcp.adapter import SchemathesisBackend
from schemathesis_mcp.models import RunRequest
from schemathesis_mcp.tools import ToolService


class ApiHandler(BaseHTTPRequestHandler):
    schema: bytes

    def do_GET(self) -> None:
        if self.path == "/openapi.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.schema)
        elif self.path == "/boom":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"secret":"response-secret"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:
        pass


@pytest.fixture
def api_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    ApiHandler.schema = json.dumps(
        {
            "openapi": "3.0.0",
            "info": {"title": "Broken API", "version": "1.0"},
            "servers": [{"url": base_url}],
            "paths": {
                "/boom": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {"application/json": {"schema": {"type": "object"}}},
                            }
                        }
                    }
                }
            },
        }
    ).encode()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_real_engine_run_produces_sanitized_failure_artifact(tmp_path, api_server) -> None:
    service = ToolService.create(backend=SchemathesisBackend(), artifact_root=tmp_path)
    started = service.start_run(
        schema=f"{api_server}/openapi.json",
        headers={"Authorization": "Bearer test-secret"},
        phases=["fuzzing"],
        generation_modes=["positive"],
        max_examples=1,
        max_failures=1,
        seed=1,
    )
    service.runs.wait(started["run_id"], timeout=20)

    result = service.get_result(started["run_id"])
    assert result["outcome"] == "failed"
    assert result["failure_ids"]
    assert set(result["artifacts"]) == {"events", "junit", "har"}

    failure = service.get_failure(started["run_id"], result["failure_ids"][0])
    assert failure["operation"] == "GET /boom"
    assert failure["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert "test-secret" not in json.dumps(failure)


def test_graphql_schema_inspection(tmp_path) -> None:
    schema_path = tmp_path / "schema.graphql"
    schema_path.write_text("type Query { hello: String! }")

    result = SchemathesisBackend().inspect(RunRequest(schema=str(schema_path), base_url="http://127.0.0.1:1/graphql"))

    assert result["specification"].startswith("GraphQL")
    assert [operation["label"] for operation in result["operations"]] == ["Query.hello"]
