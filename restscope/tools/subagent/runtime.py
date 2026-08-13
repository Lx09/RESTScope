"""Define and bind the global asynchronous Subagent control protocol.

Inputs are untrusted model data. Each implementation constructs its complete
Pydantic input before invoking Harness callbacks, then validates the callback's
result against the same DTO that supplies the global output JSON Schema.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from restscope.agent import AgentCompletion, AgentError
from restscope.llm import ToolSpec
from restscope.tools.runtime import ToolBinding

SUBAGENT_START_TOOL_NAME = "subagent.start"
SUBAGENT_WAIT_TOOL_NAME = "subagent.wait"
SUBAGENT_CANCEL_TOOL_NAME = "subagent.cancel"


class _StartInput(BaseModel):
    """Select one authorized child Profile and its only objective."""

    model_config = ConfigDict(extra="forbid")
    profile_name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=12_000)


class _StartOutput(BaseModel):
    """Identify one atomically reserved asynchronous child."""

    model_config = ConfigDict(extra="forbid")
    subagent_id: str = Field(min_length=1, max_length=160)
    profile_name: str = Field(min_length=1, max_length=120)
    status: Literal["queued"]
    depth: int = Field(ge=1, le=3)


class _WaitInput(BaseModel):
    """Wait briefly for one to three unique direct children."""

    model_config = ConfigDict(extra="forbid")
    subagent_ids: tuple[str, ...] = Field(min_length=1, max_length=3)
    timeout_seconds: int = Field(default=10, ge=1, le=60)

    @field_validator("subagent_ids")
    @classmethod
    def require_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous repeated wait targets before accessing state."""
        if len(values) != len(set(values)):
            raise ValueError("Subagent IDs must be unique")
        if any(not value.strip() or len(value) > 160 for value in values):
            raise ValueError("Subagent IDs must be 1-160 characters")
        return values


class _AgentSnapshot(BaseModel):
    """Return bounded current state for one requested direct child."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "status": {"enum": ["queued", "running"]},
                        "completion": {"type": "null"},
                        "error": {"type": "null"},
                    }
                },
                {
                    "properties": {
                        "status": {"const": "completed"},
                        "completion": {"not": {"type": "null"}},
                        "error": {"type": "null"},
                    },
                    "required": ["completion"],
                },
                {
                    "properties": {
                        "status": {"enum": ["failed", "cancelled"]},
                        "completion": {"type": "null"},
                        "error": {"not": {"type": "null"}},
                    },
                    "required": ["error"],
                },
            ]
        },
    )
    subagent_id: str = Field(min_length=1, max_length=160)
    profile_name: str = Field(min_length=1, max_length=120)
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    completion: AgentCompletion | None = None
    error: AgentError | None = None

    @model_validator(mode="after")
    def require_status_payload(self) -> _AgentSnapshot:
        """Keep lifecycle state and optional terminal payloads consistent."""
        if self.status == "completed":
            if self.completion is None or self.error is not None:
                raise ValueError("Completed Subagent requires completion only")
        elif self.status in {"failed", "cancelled"}:
            if self.error is None or self.completion is not None:
                raise ValueError("Failed or cancelled Subagent requires error only")
        elif self.completion is not None or self.error is not None:
            raise ValueError("Open Subagent cannot have a terminal payload")
        return self


class _WaitOutput(BaseModel):
    """Return every requested child after the first state change or timeout."""

    model_config = ConfigDict(extra="forbid")
    timed_out: bool
    agents: tuple[_AgentSnapshot, ...] = Field(min_length=1, max_length=3)


class _CancelInput(BaseModel):
    """Request cooperative cancellation of one direct child."""

    model_config = ConfigDict(extra="forbid")
    subagent_id: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)


class _CancelOutput(BaseModel):
    """Report whether cancellation was requested or unnecessary."""

    model_config = ConfigDict(extra="forbid")
    subagent_id: str = Field(min_length=1, max_length=160)
    status: Literal["cancellation_requested", "already_terminal"]


@dataclass(frozen=True)
class SubagentToolCallbacks:
    """Bind all three Tools to one parent Agent's private tree view."""

    start: Callable[[str, str], dict]
    wait: Callable[[tuple[str, ...], int], dict]
    cancel: Callable[[str, str | None], dict]


def subagent_start_tool_spec() -> ToolSpec:
    """Return the complete contract for atomically starting one child."""
    return ToolSpec(
        name=SUBAGENT_START_TOOL_NAME,
        description=(
            "Start one explicitly authorized Subagent Profile asynchronously for "
            "one bounded objective. The returned ID belongs only to this parent."
        ),
        kind="local_function",
        input_schema=_StartInput.model_json_schema(),
        output_schema=_StartOutput.model_json_schema(),
    )


def subagent_wait_tool_spec() -> ToolSpec:
    """Return the complete contract for bounded direct-child waiting."""
    return ToolSpec(
        name=SUBAGENT_WAIT_TOOL_NAME,
        description=(
            "Wait for one to three direct Subagents. Timeout returns current state "
            "without cancellation; terminal results are collected on return."
        ),
        kind="local_function",
        input_schema=_WaitInput.model_json_schema(),
        output_schema=_WaitOutput.model_json_schema(),
    )


def subagent_cancel_tool_spec() -> ToolSpec:
    """Return the complete contract for cooperative child cancellation."""
    return ToolSpec(
        name=SUBAGENT_CANCEL_TOOL_NAME,
        description=(
            "Request cooperative cancellation of one direct Subagent. In-flight "
            "provider or Tool work may finish its own bounded call first."
        ),
        kind="local_function",
        input_schema=_CancelInput.model_json_schema(),
        output_schema=_CancelOutput.model_json_schema(),
    )


def subagent_tool_bindings(callbacks: SubagentToolCallbacks) -> tuple[ToolBinding, ...]:
    """Create the exact three implementations for one parent Agent session."""

    def start(**arguments) -> dict:
        value = _StartInput.model_validate(arguments)
        output = callbacks.start(value.profile_name, value.objective)
        return {"structured": _StartOutput.model_validate(output).model_dump(mode="json")}

    def wait(**arguments) -> dict:
        value = _WaitInput.model_validate(arguments)
        output = callbacks.wait(value.subagent_ids, value.timeout_seconds)
        return {"structured": _WaitOutput.model_validate(output).model_dump(mode="json")}

    def cancel(**arguments) -> dict:
        value = _CancelInput.model_validate(arguments)
        output = callbacks.cancel(value.subagent_id, value.reason)
        return {"structured": _CancelOutput.model_validate(output).model_dump(mode="json")}

    return (
        ToolBinding(name=SUBAGENT_START_TOOL_NAME, execute=start),
        ToolBinding(name=SUBAGENT_WAIT_TOOL_NAME, execute=wait),
        ToolBinding(name=SUBAGENT_CANCEL_TOOL_NAME, execute=cancel),
    )
