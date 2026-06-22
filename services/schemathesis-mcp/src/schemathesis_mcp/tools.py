from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemathesis_mcp.adapter import SchemathesisBackend
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
        resolved_backend = backend or SchemathesisBackend()
        root = artifact_root or Path(tempfile.mkdtemp(prefix="schemathesis-mcp-"))
        artifacts = ArtifactStore(root)
        return cls(
            backend=resolved_backend,
            artifacts=artifacts,
            runs=RunManager(backend=resolved_backend, artifacts=artifacts),
        )

    def inspect_api(self, **kwargs: Any) -> dict[str, Any]:
        return self.backend.inspect(RunRequest.model_validate(kwargs))

    def start_run(self, **kwargs: Any) -> dict[str, Any]:
        request = RunRequest.model_validate(kwargs)
        run_id = self.runs.start(request)
        return {"run_id": run_id, "state": "queued"}

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id).model_dump(mode="json")

    def get_events(self, run_id: str, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        return self.artifacts.get_events(run_id, cursor=cursor, limit=limit).model_dump(mode="json")

    def get_result(self, run_id: str) -> dict[str, Any]:
        return self.runs.get_result(run_id).model_dump(mode="json")

    def get_failure(self, run_id: str, failure_id: str) -> dict[str, Any]:
        return self.artifacts.read_failure(run_id, failure_id).model_dump(mode="json")

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.cancel(run_id).model_dump(mode="json")

    def replay_failure(self, run_id: str, failure_id: str) -> dict[str, Any]:
        return self.backend.replay(run_id, failure_id)

    def read_resource(self, uri: str) -> str:
        return self.artifacts.read_resource(uri)
