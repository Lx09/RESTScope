"""Memory selection and budget fitting."""

from __future__ import annotations

from .schemas import MemoryItem


class MemoryCompressor:
    """Dedupe memory and fit it into a rough token budget."""

    def fit_budget(self, items: list[MemoryItem], token_budget: int) -> list[MemoryItem]:
        selected: list[MemoryItem] = []
        seen_sources: set[tuple[str, str]] = set()
        used_tokens = 0

        for item in items:
            source_key = (item.source_table, item.source_id)
            if source_key in seen_sources:
                continue
            if item.estimated_tokens > token_budget and not selected:
                selected.append(item.model_copy(update={"estimated_tokens": token_budget}))
                break
            if used_tokens + item.estimated_tokens > token_budget:
                continue
            selected.append(item)
            seen_sources.add(source_key)
            used_tokens += item.estimated_tokens

        return selected
