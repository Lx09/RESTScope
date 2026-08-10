"""Define the narrow runtime services needed by a generic Agent session.

The Agent owns these Interfaces because it is the consumer. The deterministic
Harness supplies adapters for authorized Tool execution and tree-wide model,
budget, concurrency, and cancellation control. This direction keeps the Agent
independent of concrete Harness and Toolbox implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from restscope.llm import LLMResponse, ToolCall, ToolResult


class BudgetChargeView(Protocol):
    """Expose only the budget outcome fields the Agent loop must interpret."""

    exceeded: bool
    reminder_percentages: tuple[int, ...]


class AgentToolExecutor(Protocol):
    """Execute one already-authorized Tool call and return its safe result."""

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Validate and execute one model-selected Tool call."""


class AgentTreeControlPort(Protocol):
    """Provide mechanical tree control without exposing Harness state."""

    def invoke_model(self, action: Callable, /, *args, **kwargs) -> LLMResponse:
        """Run one provider call under the shared active-agent limit."""

    def charge_response(self, response: LLMResponse) -> BudgetChargeView:
        """Charge one response against the tree-wide rollout budget."""

    def execute_tool(
        self,
        name: str,
        action: Callable,
        /,
        *args,
        **kwargs,
    ) -> ToolResult:
        """Run one Tool call under the tree's execution policy."""

    def close_descendants(self, owner_id: str) -> None:
        """Cooperatively cancel every descendant of one Agent session."""

    def close(self) -> None:
        """Close the complete Main-owned Agent tree."""
