"""Render ContextPackage messages."""

from __future__ import annotations

from .schemas import ContextBuildRequest, ContextMessage, ContextPackage, ContextSection, OutputContract, estimate_tokens


class PromptRenderer:
    """
    Coordinate prompt renderer behavior for bounded prompt-context construction.

    Read the public methods as the supported lifecycle and treat underscore-prefixed
    helpers as internal implementation details.
    """
    def render(
        self,
        *,
        request: ContextBuildRequest,
        prompt_version: str,
        sections: list[ContextSection],
        output_contract: OutputContract,
        source_refs: dict[str, list[str]],
        cycle_index: int,
        token_budget: int,
        context_id: str,
    ) -> ContextPackage:
        """
        Handle render as part of bounded prompt-context construction.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        system_message = self._render_system_message(request.role, output_contract)
        user_message = self._render_user_message(request.role, sections)
        messages = [
            ContextMessage(role="system", content=system_message),
            ContextMessage(role="user", content=user_message),
        ]
        estimated_tokens = sum(section.estimated_tokens for section in sections) + estimate_tokens(
            system_message,
            user_message,
        )
        return ContextPackage(
            id=context_id,
            task_id=request.task_id,
            schema_id=request.schema_id,
            role=request.role,
            cycle_index=cycle_index,
            prompt_version=prompt_version,
            model_name=request.model_name,
            sections=sections,
            messages=messages,
            output_contract=output_contract,
            source_refs=source_refs,
            estimated_tokens=min(estimated_tokens, token_budget),
            token_budget=token_budget,
        )

    def _render_system_message(self, role: str, output_contract: OutputContract) -> str:
        return "\n".join(
            [
                f"You are the {role} for an automated REST API testing agent.",
                "You must use only the provided context.",
                "You must output only structured JSON matching the required schema.",
                "Do not claim that actions were executed unless the context says so.",
                "Do not modify database state.",
                "Do not invent operation IDs, campaign IDs, or observation IDs.",
                f"Required output contract: {output_contract.name}.",
            ]
        )

    def _render_user_message(self, role: str, sections: list[ContextSection]) -> str:
        body = "\n\n".join(f"## {section.title}\n\n{section.content}" for section in sections)
        return f"# {role} Context\n\n{body}"
