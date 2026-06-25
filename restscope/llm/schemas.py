"""Shared schemas for provider-neutral LLM calls."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


LLMProviderName = Literal["fake", "openai_compatible", "anthropic"]
LLMRole = Literal["system", "user", "assistant", "tool"]
LLMResponseFormat = Literal["text", "json", "json_schema"]
ToolKind = Literal["local_function", "mcp_tool", "skill", "provider_builtin"]
ToolRiskLevel = Literal["low", "medium", "high"]
ToolResultStatus = Literal["succeeded", "failed", "denied", "timed_out", "approval_required"]


class LLMMessage(BaseModel):
    """A provider-neutral chat message."""

    role: LLMRole
    content: str
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    """Description of a callable capability that may be offered to a model."""

    name: str
    description: str
    kind: ToolKind
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    risk_level: ToolRiskLevel = "low"
    read_only: bool = True
    requires_approval: bool = False
    timeout_seconds: int = 30
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A model-requested tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    raw: dict[str, Any] | None = None


class ToolResult(BaseModel):
    """Sanitized result returned to the model after tool execution."""

    tool_call_id: str
    name: str
    status: ToolResultStatus
    content: str | None = None
    structured: Any | None = None
    error: dict[str, Any] | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    """Provider-neutral request consumed by LLMClient."""

    provider: str
    model: str
    messages: list[LLMMessage]
    temperature: float = 0.0
    max_tokens: int = 2048
    response_format: LLMResponseFormat = "json_schema"
    json_schema: dict[str, Any] | None = None
    json_schema_name: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: str = "none"
    timeout_seconds: int = 60
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Provider-neutral response returned by LLMClient."""

    provider: str
    model: str
    content: str | None = None
    parsed_json: Any | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMModelConfig(BaseModel):
    """Model settings selected for one context role."""

    role: str
    provider: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_seconds: int = 60
    response_format: LLMResponseFormat = "json_schema"
    tool_choice: str = "none"
    enabled: bool = True


class ValidationIssue(BaseModel):
    """One structured output validation issue."""

    type: str
    message: str
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    """Result of converting an LLM response into a typed output object."""

    valid: bool
    validated_object: Any | None = None
    errors: list[ValidationIssue] = Field(default_factory=list)
    raw_json: Any | None = None
