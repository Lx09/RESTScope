"""Deterministic memory ranking."""

from __future__ import annotations

from .schemas import MemoryItem, MemoryQuery


class MemoryRanker:
    """Rank memory using the design score formula."""

    def rank(self, items: list[MemoryItem], query: MemoryQuery) -> list[MemoryItem]:
        return sorted(items, key=lambda item: self.score(item, query), reverse=True)

    def score(self, item: MemoryItem, query: MemoryQuery) -> float:
        relevance = item.relevance_score
        if item.operation_id and item.operation_id in query.operation_ids:
            relevance = max(relevance, 1.0)
        if query.focus_keywords:
            haystack = f"{item.title} {item.content}".lower()
            if any(keyword.lower() in haystack for keyword in query.focus_keywords):
                relevance = max(relevance, 0.9)
        return (
            0.35 * relevance
            + 0.25 * item.importance
            + 0.20 * item.confidence
            + 0.10 * item.recency_score
            + 0.10 * item.risk_score
        )
