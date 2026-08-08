"""Expose asynchronous child-Agent control as three global Tools.

The Tool Module owns the complete model contract and validates both arguments
and successful results with Pydantic. The Harness supplies callbacks bound to
one parent Agent, so a model can never choose a different ownership scope.
"""

from .runtime import (
    SUBAGENT_CANCEL_TOOL_NAME,
    SUBAGENT_START_TOOL_NAME,
    SUBAGENT_WAIT_TOOL_NAME,
    SubagentToolCallbacks,
    subagent_cancel_tool_spec,
    subagent_start_tool_spec,
    subagent_tool_bindings,
    subagent_wait_tool_spec,
)

__all__ = [
    "SUBAGENT_CANCEL_TOOL_NAME",
    "SUBAGENT_START_TOOL_NAME",
    "SUBAGENT_WAIT_TOOL_NAME",
    "SubagentToolCallbacks",
    "subagent_cancel_tool_spec",
    "subagent_start_tool_spec",
    "subagent_tool_bindings",
    "subagent_wait_tool_spec",
]
