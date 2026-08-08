"""Generic Agent contracts and Profile Interface for Main and child sessions.

The deterministic Harness is the only constructor for :class:`Agent`. Existing
named LLM Agents remain temporary migration exceptions and use the same Profile
vocabulary without inheriting from the generic runtime.
"""

from .contracts import (
    AgentCompletion,
    AgentError,
    AgentFinding,
    AgentResult,
    AgentTask,
    AgentUsage,
)
from .profile import AgentProfile
from .registry import AgentProfileRegistry
from .runtime import Agent

__all__ = [
    "Agent",
    "AgentCompletion",
    "AgentError",
    "AgentFinding",
    "AgentProfile",
    "AgentProfileRegistry",
    "AgentResult",
    "AgentTask",
    "AgentUsage",
]
