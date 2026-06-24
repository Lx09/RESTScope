"""Observation memory from test_observations."""

from __future__ import annotations

from decimal import Decimal

from restscope.db.records import TestObservationRecord
from restscope.db.repositories import ArtifactRepository, TestObservationRepository

from ..schemas import MemoryItem


OPEN_OBSERVATION_STATUSES = ["observed", "normalized", "deduplicated", "triaging", "confirmed_issue", "regressed"]


class ObservationMemoryStore:
    def __init__(
        self,
        observation_repo: TestObservationRepository,
        artifact_repo: ArtifactRepository,
    ) -> None:
        self.observation_repo = observation_repo
        self.artifact_repo = artifact_repo

    def list_recent_for_operations(
        self,
        schema_id: str,
        operation_ids: list[str],
        limit_per_operation: int,
    ) -> list[MemoryItem]:
        records = self.observation_repo.list_recent_for_operations(
            schema_id,
            operation_ids,
            limit=max(1, limit_per_operation) * max(1, len(operation_ids)),
        )
        return [self._item(record) for record in records if record.status != "ignored"]

    def list_open_issues(self, schema_id: str, limit: int) -> list[MemoryItem]:
        return [
            self._item(record)
            for record in self.observation_repo.list_by_schema_status(
                schema_id,
                OPEN_OBSERVATION_STATUSES,
                limit=limit,
            )
        ]

    def list_regression_candidates(self, schema_id: str, limit: int) -> list[MemoryItem]:
        return [
            self._item(record)
            for record in self.observation_repo.list_by_schema_status(
                schema_id,
                ["confirmed_issue", "regressed", "observed"],
                limit=limit,
            )
        ]

    def _item(self, observation: TestObservationRecord) -> MemoryItem:
        confidence = _decimal_to_float(observation.confidence)
        severity_importance = {"critical": 1.0, "high": 0.9, "medium": 0.7, "low": 0.4}
        importance = max(
            severity_importance.get(observation.severity, 0.5),
            min(1.0, 0.4 + observation.occurrence_count * 0.1),
        )
        request_summary = _safe_summary(observation.request_summary_json)
        response_summary = _safe_summary(observation.response_summary_json)
        content = (
            f"{observation.observation_type} severity={observation.severity}, "
            f"status={observation.status}, seen {observation.occurrence_count} times. "
            f"Request summary: {request_summary}. Response summary: {response_summary}."
        )
        return MemoryItem(
            id=f"mem_obs_{observation.id}",
            kind="observation",
            schema_id=observation.schema_id,
            task_id=observation.task_id,
            operation_id=observation.operation_id,
            campaign_id=observation.campaign_id,
            observation_id=observation.id,
            title=f"{observation.observation_type} on {observation.operation_id or 'unknown operation'}",
            content=content,
            structured={
                "observation_type": observation.observation_type,
                "severity": observation.severity,
                "confidence": confidence,
                "dedupe_key": observation.dedupe_key,
                "occurrence_count": observation.occurrence_count,
                "status": observation.status,
                "request_summary": request_summary,
                "response_summary": response_summary,
                "reproducer_artifact_id": observation.reproducer_artifact_id,
                "raw_artifact_id": observation.raw_artifact_id,
            },
            importance=importance,
            confidence=confidence,
            recency_score=0.8,
            relevance_score=0.8,
            risk_score=importance,
            source_table="test_observations",
            source_id=observation.id,
        )


def _safe_summary(summary: dict | None) -> dict:
    if not summary:
        return {}
    return {key: value for key, value in summary.items() if key != "raw"}


def _decimal_to_float(value: Decimal) -> float:
    return float(value)
