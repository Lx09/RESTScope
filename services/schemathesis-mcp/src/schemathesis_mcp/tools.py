"""Service facade for MCP tool calls.

This module sits between the FastMCP server registration layer and the lower
level run, artifact, and Schemathesis CLI backend components. `server.py`
registers public MCP tools, then delegates each call here so this layer can
validate inputs, prepare deployment-level configuration, create the artifact
store, manage run lifecycle state, and expose safe capability metadata.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from schemathesis_mcp.adapter import CliBackend
from schemathesis_mcp.artifacts import ArtifactStore
from schemathesis_mcp.models import RunRequest
from schemathesis_mcp.runs import RunManager


@dataclass
class ToolService:
    backend: Any
    artifacts: ArtifactStore
    runs: RunManager

    @classmethod
    def create(cls, backend: Any | None = None, artifact_root: Path | None = None) -> ToolService:
        resolved_backend = backend or CliBackend()
        probe = getattr(resolved_backend, "probe", None)
        if probe is not None:
            probe()
        root = artifact_root or Path(tempfile.mkdtemp(prefix="schemathesis-mcp-"))
        artifacts = ArtifactStore(root)
        return cls(
            backend=resolved_backend,
            artifacts=artifacts,
            runs=RunManager(backend=resolved_backend, artifacts=artifacts),
        )

    def get_capabilities(self) -> dict[str, Any]:
        probe = getattr(self.backend, "probe", None)
        backend_info = probe() if probe is not None else {}
        return {
            "name": "schemathesis-mcp",
            "version": _package_version(),
            "transport": "stdio",
            "backend": {
                "type": "schemathesis-cli",
                "cli_version": backend_info.get("version"),
                "command_overridden": _env_configured("SCHEMATHESIS_CLI"),
            },
            "tools": [
                "get_capabilities",
                "start_run",
                "get_run",
                "get_events",
                "get_result",
                "get_failure",
                "cancel_run",
            ],
            "resources": [
                "schemathesis://runs/{run_id}/{name}",
                "schemathesis://runs/{run_id}/failures/{failure_id}.json",
            ],
            "schema_inputs": {
                "kinds": ["file", "url", "inline"],
                "inline_formats": ["yaml", "json"],
            },
            "run_options": {
                "reports": ["junit", "har", "vcr", "allure"],
                "supports_headers": True,
                "supports_tls_verify": True,
                "supports_filters": True,
                "supports_timeout": True,
                "supports_seed": True,
            },
            "limits": {
                "max_concurrent_runs": self.runs.max_concurrent,
                "artifact_ttl_seconds": self.runs.artifact_ttl_seconds,
            },
            "configuration": {
                "env": [
                    {
                        "name": "SCHEMATHESIS_CLI",
                        "required": False,
                        "configured": _env_configured("SCHEMATHESIS_CLI"),
                        "purpose": "Override the Schemathesis CLI command",
                    },
                    {
                        "name": "SCHEMATHESIS_MCP_ALLOWED_PATHS",
                        "required": False,
                        "configured": _env_configured("SCHEMATHESIS_MCP_ALLOWED_PATHS"),
                        "purpose": "Add allowed local schema roots",
                    },
                    {
                        "name": "SCHEMATHESIS_MCP_ALLOWED_HOSTS",
                        "required": False,
                        "configured": _env_configured("SCHEMATHESIS_MCP_ALLOWED_HOSTS"),
                        "purpose": "Restrict URL schema and base_url hosts",
                    },
                ],
                "path_policy": {
                    "default_allows_current_working_directory": True,
                    "additional_roots_configured": _env_configured("SCHEMATHESIS_MCP_ALLOWED_PATHS"),
                },
                "target_policy": {
                    "host_allowlist_configured": _env_configured("SCHEMATHESIS_MCP_ALLOWED_HOSTS"),
                },
            },
        }

    def start_run(self, **kwargs: Any) -> dict[str, Any]:
        request = RunRequest.model_validate(kwargs)
        run_id = self.runs.start(request)
        return {"run_id": run_id, "state": "queued"}

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id).model_dump(mode="json")

    def get_events(self, run_id: str, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.artifacts.get_events(run_id, cursor=cursor, limit=limit).model_dump(mode="json")

    def get_result(self, run_id: str) -> dict[str, Any]:
        return self.runs.get_result(run_id).model_dump(mode="json", by_alias=True)

    def get_failure(self, run_id: str, failure_id: str) -> dict[str, Any]:
        return self.artifacts.read_failure(run_id, failure_id).model_dump(mode="json")

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.cancel(run_id).model_dump(mode="json")

    def read_resource(self, uri: str) -> str:
        return self.artifacts.read_resource(uri)


def _package_version() -> str:
    try:
        return version("schemathesis-mcp")
    except PackageNotFoundError:
        return "0.1.0"


def _env_configured(name: str) -> bool:
    return bool(os.getenv(name))
