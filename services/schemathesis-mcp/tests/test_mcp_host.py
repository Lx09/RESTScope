"""End-to-end tests for MCP host/client stdio integration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = [
    "get_capabilities",
    "start_run",
    "get_run",
    "get_events",
    "get_result",
    "get_failure",
    "cancel_run",
]


@pytest.fixture
def api_server() -> Iterator[tuple[int, list[str]]]:
    hits: list[str] = []

    class ApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, hits
    finally:
        server.shutdown()
        thread.join(timeout=2)


@pytest.fixture
def contract_dir(tmp_path: Path) -> Path:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "openapi.yaml").write_text(
        """
openapi: 3.0.3
info:
  title: MCP Host E2E
  version: 1.0.0
paths:
  /ok:
    get:
      operationId: getOk
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema:
                type: object
                properties:
                  ok:
                    type: boolean
                required:
                  - ok
""".strip(),
        encoding="utf-8",
    )
    return contracts


def test_local_stdio_mcp_host_runs_api_test(
    tmp_path: Path, contract_dir: Path, api_server: tuple[int, list[str]]
) -> None:
    port, hits = api_server
    artifacts = tmp_path / "artifacts"
    env = {
        **os.environ,
        "PYTHONPATH": _pythonpath_with_src(),
        "NO_PROXY": "localhost,127.0.0.1",
        "SCHEMATHESIS_MCP_ALLOWED_PATHS": str(contract_dir),
        "SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "SCHEMATHESIS_MCP_ARTIFACT_DIR": str(artifacts),
        "SCHEMATHESIS_MCP_ARTIFACT_TTL": "1h",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "schemathesis_mcp.server"],
        env=env,
        cwd=REPO_ROOT,
    )

    anyio.run(
        _run_mcp_host_e2e,
        params,
        {"kind": "file", "path": str(contract_dir / "openapi.yaml")},
        f"http://127.0.0.1:{port}",
        artifacts,
        hits,
    )


def test_docker_stdio_mcp_host_runs_api_test(
    tmp_path: Path, contract_dir: Path, api_server: tuple[int, list[str]]
) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is not installed")

    _docker_build(docker)
    port, hits = api_server
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    params = StdioServerParameters(
        command=docker,
        args=[
            "run",
            "--rm",
            "-i",
            "-v",
            f"{contract_dir}:/workspace:ro",
            "-v",
            f"{artifacts}:/data",
            "-e",
            "SCHEMATHESIS_MCP_ALLOWED_HOSTS=host.docker.internal,localhost,127.0.0.1",
            "schemathesis-mcp:test",
        ],
    )

    anyio.run(
        _run_mcp_host_e2e,
        params,
        {"kind": "file", "path": "/workspace/openapi.yaml"},
        f"http://host.docker.internal:{port}",
        artifacts,
        hits,
    )


async def _run_mcp_host_e2e(
    params: StdioServerParameters,
    schema: dict[str, Any],
    base_url: str,
    artifacts: Path,
    hits: list[str],
) -> None:
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        assert [tool.name for tool in tools.tools] == EXPECTED_TOOLS

        capabilities = _payload(await session.call_tool("get_capabilities", {}))
        artifact_policy = capabilities["configuration"]["artifact_policy"]
        assert artifact_policy["persistent_root_configured"] is True
        assert artifact_policy["ttl_seconds"] == 3600

        started = _payload(
            await session.call_tool(
                "start_run",
                {
                    "schema": schema,
                    "base_url": base_url,
                    "phases": ["fuzzing"],
                    "generation_modes": ["positive"],
                    "max_examples": 1,
                    "max_failures": 1,
                    "seed": 1,
                },
            )
        )
        run_id = started["run_id"]

        run = await _wait_for_finished_run(session, run_id)
        assert run["state"] == "completed"

        result = _payload(await session.call_tool("get_result", {"run_id": run_id}))
        assert result["run_id"] == run_id
        assert result["outcome"] == "passed"

        events = _payload(await session.call_tool("get_events", {"run_id": run_id, "cursor": 0, "limit": 10}))
        assert events["events"]
        assert events["artifact_uri"] == f"schemathesis://runs/{run_id}/events.ndjson"

        run_dir = artifacts / run_id
        assert run_dir.is_dir()
        existing = {path.name for path in run_dir.iterdir()}
        assert {"result.json", "events.ndjson", "schemathesis.ndjson", "schema.yaml"} <= existing
        assert "/ok" in hits


async def _wait_for_finished_run(session: ClientSession, run_id: str) -> dict[str, Any]:
    for _ in range(120):
        run = _payload(await session.call_tool("get_run", {"run_id": run_id}))
        if run["state"] in {"completed", "failed", "cancelled"}:
            return run
        await anyio.sleep(0.25)
    pytest.fail(f"Run {run_id} did not finish in time")


def _payload(result: Any) -> dict[str, Any]:
    if result.structuredContent is not None:
        data = result.structuredContent
    elif result.content and hasattr(result.content[0], "text"):
        data = json.loads(result.content[0].text)
    else:
        raise AssertionError(f"Tool result has no structured payload: {result!r}")
    return data.get("result", data) if isinstance(data, dict) else data


def _docker_build(docker: str) -> None:
    result = subprocess.run(
        [docker, "build", "-t", "schemathesis-mcp:test", "."],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "Docker build failed\n\n"
            f"STDOUT:\n{result.stdout}\n\n"
            f"STDERR:\n{result.stderr}"
        )


def _pythonpath_with_src() -> str:
    src = str(REPO_ROOT / "src")
    current = os.environ.get("PYTHONPATH")
    return src if not current else f"{src}{os.pathsep}{current}"
