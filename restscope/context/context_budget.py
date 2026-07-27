"""Token budget fitting for context sections."""

from __future__ import annotations

from .schemas import ContextSection, estimate_tokens


class ContextBudgetManager:
    """
    Coordinate context budget manager behavior for bounded prompt-context construction.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    def fit(self, sections: list[ContextSection], token_budget: int) -> list[ContextSection]:
        """
        Handle fit as part of bounded prompt-context construction.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        required = [section for section in sections if section.required]
        optional = sorted(
            [section for section in sections if not section.required],
            key=lambda section: section.priority,
            reverse=True,
        )
        selected = [*required, *optional]
        total = sum(section.estimated_tokens for section in selected)
        if total <= token_budget:
            return selected

        selected = [*required]
        used = sum(section.estimated_tokens for section in selected)
        for section in optional:
            if used + section.estimated_tokens <= token_budget:
                selected.append(section)
                used += section.estimated_tokens

        if used <= token_budget:
            return selected
        return self._compress_required(selected, token_budget)

    def _compress_required(self, sections: list[ContextSection], token_budget: int) -> list[ContextSection]:
        if not sections:
            return []
        per_section = max(1, token_budget // len(sections))
        compressed = []
        for section in sections:
            words = section.content.split()
            content = " ".join(words[:per_section])
            if len(words) > per_section:
                content = f"{content} ..."
            compressed.append(
                section.model_copy(
                    update={
                        "content": content,
                        "estimated_tokens": estimate_tokens(section.title, content),
                    }
                )
            )
        return compressed
