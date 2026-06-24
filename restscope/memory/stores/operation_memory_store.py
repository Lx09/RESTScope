"""Operation memory from operations and operation_intelligence."""

from __future__ import annotations

from decimal import Decimal

from restscope.db.records import OperationIntelligenceRecord, OperationRecord
from restscope.db.repositories import OperationIntelligenceRepository, OperationRepository

from ..schemas import MemoryItem


class OperationMemoryStore:
    def __init__(
        self,
        operation_repo: OperationRepository,
        intelligence_repo: OperationIntelligenceRepository,
    ) -> None:
        self.operation_repo = operation_repo
        self.intelligence_repo = intelligence_repo

    def list_high_risk_operations(self, schema_id: str, limit: int) -> list[MemoryItem]:
        intelligence = self.intelligence_repo.list_high_risk(schema_id, limit=limit)
        return self._items_from_intelligence(intelligence)

    def get_operation_memory(self, operation_ids: list[str]) -> list[MemoryItem]:
        if not operation_ids:
            return []
        intelligence = [
            item
            for operation_id in operation_ids
            if (item := self.intelligence_repo.get_by_operation(operation_id)) is not None
        ]
        items = self._items_from_intelligence(intelligence)
        known_ids = {item.operation_id for item in items}
        missing_ops = [
            op for op in self.operation_repo.list_by_ids(operation_ids) if op.id not in known_ids
        ]
        return [*items, *(self._item_from_operation(op, None) for op in missing_ops)]

    def list_not_recently_tested_operations(self, schema_id: str, limit: int) -> list[MemoryItem]:
        operations = self.operation_repo.list_by_schema(schema_id)[:limit]
        return [self._item_from_operation(operation, None) for operation in operations]

    def _items_from_intelligence(
        self,
        intelligence_records: list[OperationIntelligenceRecord],
    ) -> list[MemoryItem]:
        operations_by_id = {
            operation.id: operation
            for operation in self.operation_repo.list_by_ids(
                [record.operation_id for record in intelligence_records]
            )
        }
        return [
            self._item_from_operation(operations_by_id[record.operation_id], record)
            for record in intelligence_records
            if record.operation_id in operations_by_id
        ]

    def _item_from_operation(
        self,
        operation: OperationRecord,
        intelligence: OperationIntelligenceRecord | None,
    ) -> MemoryItem:
        dynamic_risk = _decimal_to_float(
            intelligence.dynamic_risk_score if intelligence else Decimal("0")
        )
        static_risk = _decimal_to_float(operation.static_risk_score)
        checks = intelligence.recommended_checks if intelligence else []
        content = (
            f"{operation.method} {operation.path}. "
            f"{operation.summary or 'No summary'}. "
            f"mutability={operation.mutability or 'unknown'}, "
            f"dynamic_risk_score={dynamic_risk:.2f}, static_risk_score={static_risk:.2f}."
        )
        return MemoryItem(
            id=f"mem_op_{operation.id}",
            kind="operation",
            schema_id=operation.schema_id,
            operation_id=operation.id,
            title=f"{operation.method} {operation.path}",
            content=content,
            structured={
                "operation_id": operation.operation_id,
                "method": operation.method,
                "path": operation.path,
                "tags": operation.tags,
                "resource": operation.resource,
                "mutability": operation.mutability,
                "request_schema_refs": operation.request_schema_refs,
                "response_schema_refs": operation.response_schema_refs,
                "static_risk_score": static_risk,
                "dynamic_risk_score": dynamic_risk,
                "failure_density": _decimal_to_float(intelligence.failure_density) if intelligence else 0.0,
                "flake_rate": _decimal_to_float(intelligence.flake_rate) if intelligence else 0.0,
                "recommended_checks": checks,
            },
            importance=max(static_risk, dynamic_risk, 0.5),
            confidence=0.8 if intelligence else 0.6,
            relevance_score=0.7,
            risk_score=max(static_risk, dynamic_risk),
            source_table="operation_intelligence" if intelligence else "operations",
            source_id=operation.id,
        )


def _decimal_to_float(value: Decimal) -> float:
    return float(value)
