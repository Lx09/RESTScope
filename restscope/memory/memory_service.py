"""Role-specific memory retrieval service."""

from __future__ import annotations

from restscope.db.unit_of_work import UnitOfWork

from .memory_compressor import MemoryCompressor
from .memory_ranker import MemoryRanker
from .schemas import MemoryItem, MemoryPackage, MemoryQuery
from .stores import (
    CampaignMemoryStore,
    EpisodicMemoryStore,
    ObservationMemoryStore,
    OperationMemoryStore,
    WorkingMemoryStore,
)


class MemoryService:
    """Read-only memory facade over DB repositories."""

    def __init__(
        self,
        working_store: WorkingMemoryStore,
        operation_store: OperationMemoryStore,
        observation_store: ObservationMemoryStore,
        campaign_store: CampaignMemoryStore,
        episodic_store: EpisodicMemoryStore,
        ranker: MemoryRanker | None = None,
        compressor: MemoryCompressor | None = None,
    ) -> None:
        self.working_store = working_store
        self.operation_store = operation_store
        self.observation_store = observation_store
        self.campaign_store = campaign_store
        self.episodic_store = episodic_store
        self.ranker = ranker or MemoryRanker()
        self.compressor = compressor or MemoryCompressor()

    @classmethod
    def from_unit_of_work(cls, uow: UnitOfWork) -> "MemoryService":
        return cls(
            working_store=WorkingMemoryStore(uow.tasks),
            operation_store=OperationMemoryStore(uow.operations, uow.intelligence),
            observation_store=ObservationMemoryStore(uow.observations, uow.artifacts),
            campaign_store=CampaignMemoryStore(uow.campaigns, uow.artifacts),
            episodic_store=EpisodicMemoryStore(uow.context_snapshots, uow.events),
        )

    def retrieve_for_planner(
        self,
        *,
        task_id: str,
        schema_id: str,
        token_budget: int,
    ) -> MemoryPackage:
        working = self.working_store.get_current_task_memory(task_id)
        selected_ids = _selected_operation_ids(working)
        high_risk_ops = self.operation_store.list_high_risk_operations(schema_id=schema_id, limit=30)
        selected_ops = self.operation_store.get_operation_memory(selected_ids)
        operation_items = _merge_unique([*high_risk_ops, *selected_ops])
        operation_ids = [item.operation_id for item in operation_items if item.operation_id]
        observations = self.observation_store.list_recent_for_operations(
            schema_id=schema_id,
            operation_ids=operation_ids,
            limit_per_operation=3,
        )
        campaigns = self.campaign_store.list_recent_campaigns(task_id=task_id, limit=5)
        episodic = self.episodic_store.list_recent_task_events(task_id=task_id, limit=10)
        snapshot = self.episodic_store.get_last_context_snapshot_ref(task_id, "planner")
        if snapshot is not None:
            episodic.append(snapshot)
        query = MemoryQuery(
            schema_id=schema_id,
            task_id=task_id,
            role="planner",
            operation_ids=operation_ids,
            token_budget=token_budget,
        )
        return self._package(query, [*working, *operation_items, *observations, *campaigns, *episodic], selected_ids)

    def retrieve_for_result_analyst(
        self,
        *,
        task_id: str,
        schema_id: str,
        campaign_id: str,
        operation_ids: list[str],
        token_budget: int,
    ) -> MemoryPackage:
        working = self.working_store.get_current_task_memory(task_id)
        operations = self.operation_store.get_operation_memory(operation_ids)
        observations = self.observation_store.list_recent_for_operations(
            schema_id=schema_id,
            operation_ids=operation_ids,
            limit_per_operation=5,
        )
        campaigns = [
            item
            for item in self.campaign_store.list_recent_campaigns(task_id=task_id, limit=10)
            if item.campaign_id == campaign_id
        ]
        episodic = self.episodic_store.list_recent_campaign_events(campaign_id=campaign_id, limit=10)
        query = MemoryQuery(
            schema_id=schema_id,
            task_id=task_id,
            campaign_id=campaign_id,
            role="result_analyst",
            operation_ids=operation_ids,
            token_budget=token_budget,
        )
        return self._package(query, [*working, *operations, *observations, *campaigns, *episodic], operation_ids)

    def retrieve_for_decision_maker(
        self,
        *,
        task_id: str,
        schema_id: str,
        token_budget: int,
    ) -> MemoryPackage:
        working = self.working_store.get_current_task_memory(task_id)
        high_risk_ops = self.operation_store.list_high_risk_operations(schema_id=schema_id, limit=20)
        observations = self.observation_store.list_open_issues(schema_id=schema_id, limit=20)
        campaigns = self.campaign_store.list_recent_campaigns(task_id=task_id, limit=10)
        episodic = self.episodic_store.list_recent_task_events(task_id=task_id, limit=15)
        operation_ids = [item.operation_id for item in high_risk_ops if item.operation_id]
        query = MemoryQuery(
            schema_id=schema_id,
            task_id=task_id,
            role="decision_maker",
            operation_ids=operation_ids,
            token_budget=token_budget,
        )
        return self._package(query, [*working, *high_risk_ops, *observations, *campaigns, *episodic], operation_ids)

    def retrieve_for_check_designer(
        self,
        *,
        task_id: str,
        schema_id: str,
        operation_ids: list[str],
        token_budget: int,
    ) -> MemoryPackage:
        operations = self.operation_store.get_operation_memory(operation_ids)
        observations = self.observation_store.list_recent_for_operations(
            schema_id=schema_id,
            operation_ids=operation_ids,
            limit_per_operation=5,
        )
        campaigns = self.campaign_store.list_recent_campaigns(task_id=task_id, limit=3)
        query = MemoryQuery(
            schema_id=schema_id,
            task_id=task_id,
            role="check_designer",
            operation_ids=operation_ids,
            token_budget=token_budget,
        )
        return self._package(query, [*operations, *observations, *campaigns], operation_ids)

    def _package(
        self,
        query: MemoryQuery,
        items: list[MemoryItem],
        selected_operation_ids: list[str],
    ) -> MemoryPackage:
        ranked = self.ranker.rank(_merge_unique(items), query)
        compressed = self.compressor.fit_budget(ranked[: query.max_items], query.token_budget)
        return MemoryPackage.from_items(
            schema_id=query.schema_id,
            task_id=query.task_id,
            role=query.role,
            items=compressed,
            selected_operation_ids=selected_operation_ids,
        )


def _selected_operation_ids(working_items: list[MemoryItem]) -> list[str]:
    if not working_items:
        return []
    return list(working_items[0].structured.get("selected_operation_ids", []))


def _merge_unique(items: list[MemoryItem]) -> list[MemoryItem]:
    result: list[MemoryItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.source_table, item.source_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
