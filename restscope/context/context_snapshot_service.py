"""Persist complete context artifacts and snapshot metadata."""

from __future__ import annotations

import json
from pathlib import Path

from restscope.db.ids import new_artifact_id, new_snapshot_id
from restscope.db.repositories import ArtifactRepository, ContextSnapshotRepository, EventLogRepository

from .schemas import ContextPackage


class LocalContextArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def write_json(self, context: ContextPackage) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{context.id}.json"
        path.write_text(
            json.dumps(context.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return f"file://{path}"


class ContextSnapshotService:
    def __init__(
        self,
        *,
        artifact_store: LocalContextArtifactStore,
        artifact_repo: ArtifactRepository,
        context_snapshot_repo: ContextSnapshotRepository,
        event_log_repo: EventLogRepository,
    ) -> None:
        self.artifact_store = artifact_store
        self.artifact_repo = artifact_repo
        self.context_snapshot_repo = context_snapshot_repo
        self.event_log_repo = event_log_repo

    def persist(self, context: ContextPackage):
        artifact_uri = self.artifact_store.write_json(context)
        artifact = self.artifact_repo.add(
            id=new_artifact_id(),
            task_id=context.task_id,
            artifact_type="context_snapshot",
            artifact_uri=artifact_uri,
            metadata_json={
                "context_id": context.id,
                "schema_id": context.schema_id,
                "role": context.role,
                "cycle_index": context.cycle_index,
                "prompt_version": context.prompt_version,
            },
        )
        snapshot = self.context_snapshot_repo.add(
            id=new_snapshot_id(),
            task_id=context.task_id,
            schema_id=context.schema_id,
            role=context.role,
            cycle_index=context.cycle_index,
            artifact_uri=artifact.artifact_uri,
            source_refs_json=context.source_refs,
            total_estimated_tokens=context.estimated_tokens,
            prompt_version=context.prompt_version,
            model_name=context.model_name or "unknown",
        )
        self.event_log_repo.append(
            task_id=context.task_id,
            event_type="context_built",
            actor="system",
            payload_json={
                "context_id": context.id,
                "context_snapshot_id": snapshot.id,
                "role": context.role,
                "cycle_index": context.cycle_index,
                "artifact_uri": artifact.artifact_uri,
                "estimated_tokens": context.estimated_tokens,
            },
        )
        return snapshot
