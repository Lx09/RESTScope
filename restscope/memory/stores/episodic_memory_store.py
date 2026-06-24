"""Episodic memory from context_snapshots and event_log."""

from __future__ import annotations

from restscope.db.records import ContextSnapshotRecord, EventLogRecord
from restscope.db.repositories import ContextSnapshotRepository, EventLogRepository

from ..schemas import MemoryItem


class EpisodicMemoryStore:
    def __init__(
        self,
        context_snapshot_repo: ContextSnapshotRepository,
        event_log_repo: EventLogRepository,
    ) -> None:
        self.context_snapshot_repo = context_snapshot_repo
        self.event_log_repo = event_log_repo

    def list_recent_task_events(self, task_id: str, limit: int) -> list[MemoryItem]:
        return [self._event_item(event) for event in self.event_log_repo.list_by_task(task_id, limit=limit)]

    def list_recent_campaign_events(self, campaign_id: str, limit: int) -> list[MemoryItem]:
        return [
            self._event_item(event)
            for event in self.event_log_repo.list_by_campaign(campaign_id, limit=limit)
        ]

    def get_last_context_snapshot_ref(self, task_id: str, role: str) -> MemoryItem | None:
        snapshot = self.context_snapshot_repo.get_latest_by_task_role(task_id, role)
        return self._snapshot_item(snapshot) if snapshot is not None else None

    def _event_item(self, event: EventLogRecord) -> MemoryItem:
        return MemoryItem(
            id=f"mem_event_{event.id}",
            kind="episodic",
            task_id=event.task_id,
            campaign_id=event.campaign_id,
            title=f"Event: {event.event_type}",
            content=f"{event.actor} emitted {event.event_type}.",
            structured={
                "event_type": event.event_type,
                "actor": event.actor,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "payload": event.payload_json,
            },
            importance=0.5,
            confidence=1.0,
            recency_score=0.9,
            source_table="event_log",
            source_id=str(event.id),
        )

    def _snapshot_item(self, snapshot: ContextSnapshotRecord) -> MemoryItem:
        return MemoryItem(
            id=f"mem_ctx_{snapshot.id}",
            kind="episodic",
            schema_id=snapshot.schema_id,
            task_id=snapshot.task_id,
            title=f"Last {snapshot.role} context snapshot",
            content=f"Context snapshot artifact: {snapshot.artifact_uri}.",
            structured={
                "role": snapshot.role,
                "cycle_index": snapshot.cycle_index,
                "artifact_uri": snapshot.artifact_uri,
                "source_refs": snapshot.source_refs_json,
                "prompt_version": snapshot.prompt_version,
                "model_name": snapshot.model_name,
            },
            importance=0.6,
            confidence=1.0,
            recency_score=0.8,
            source_table="context_snapshots",
            source_id=snapshot.id,
        )
