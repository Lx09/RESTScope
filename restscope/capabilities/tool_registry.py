"""Registry of model-visible tools and local handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from restscope.llm.schemas import ToolSpec


class ToolRegistry:
    """Keep tool specs separate from executable handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, *, spec: ToolSpec, handler: Callable[..., Any] | None = None) -> None:
        """
        Handle register as part of the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        self._tools[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    def get_spec(self, name: str) -> ToolSpec:
        """
        Return spec for the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._tools[name]

    def get_handler(self, name: str) -> Callable[..., Any]:
        """
        Return handler for the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self._handlers[name]

    def list_specs(self) -> list[ToolSpec]:
        """
        Return specs for the policy-controlled model tool boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return list(self._tools.values())
