import json

from schemathesis_mcp.adapter import SchemathesisBackend
from schemathesis_mcp.models import RunRequest

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Example", "version": "1.0"},
    "servers": [{"url": "http://127.0.0.1:1"}],
    "paths": {
        "/users": {
            "get": {
                "operationId": "listUsers",
                "tags": ["users"],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def test_inspect_api_loads_local_openapi_and_lists_operations(tmp_path) -> None:
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(json.dumps(OPENAPI))
    backend = SchemathesisBackend()

    result = backend.inspect(RunRequest(schema=str(schema_path)))

    assert result["title"] == "Example"
    assert result["specification"].startswith("Open API")
    assert result["operations"] == [
        {
            "label": "GET /users",
            "method": "GET",
            "path": "/users",
            "operation_id": "listUsers",
            "tags": ["users"],
        }
    ]
    assert result["statistics"]["operations"]["selected"] == 1


def test_inspect_api_applies_operation_filters(tmp_path) -> None:
    schema = {
        **OPENAPI,
        "paths": {
            **OPENAPI["paths"],
            "/health": {"get": {"responses": {"200": {"description": "OK"}}}},
        },
    }
    schema_path = tmp_path / "openapi.json"
    schema_path.write_text(json.dumps(schema))
    backend = SchemathesisBackend()

    result = backend.inspect(RunRequest(schema=str(schema_path), include={"path": "/health"}))

    assert [operation["path"] for operation in result["operations"]] == ["/health"]
