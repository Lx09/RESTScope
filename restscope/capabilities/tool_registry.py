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
        self._tools[spec.name] = spec
        if handler is not None:
            self._handlers[spec.name] = handler

    def get_spec(self, name: str) -> ToolSpec:
        return self._tools[name]

    def get_handler(self, name: str) -> Callable[..., Any]:
        return self._handlers[name]

    def list_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())
