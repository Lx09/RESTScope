"""Shared schemas for provider-neutral LLM calls."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


LLMRole = Literal["system", "developer", "user", "assistant", "tool"]
LLMResponseFormat = Literal["text", "json", "json_schema"]
LLMReasoningMode = Literal["default", "enabled", "disabled"]
LLMReasoningEffort = Literal["high", "max"]
ToolKind = Literal["local_function", "mcp_tool", "skill", "provider_builtin"]
ToolResultStatus = Literal["succeeded", "failed", "denied", "timed_out"]


class ToolCall(BaseModel):
    """A model-requested tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    provider_context: dict[str, Any] = Field(default_factory=dict, repr=False)


class LLMReasoningConfig(BaseModel):
    """Provider-neutral reasoning controls for one model request."""

    mode: LLMReasoningMode = "default"
    effort: LLMReasoningEffort | None = None


class LLMMessage(BaseModel):
    """A provider-neutral chat message."""

    role: LLMRole
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolSpec(BaseModel):
    """Description of a callable capability that may be offered to a model.

    ``strict`` asks compatible providers to validate generated function
    arguments against ``input_schema`` before returning the call. It defaults
    off so existing runtime tools keep their current provider behavior.
    """

    name: str
    description: str
    kind: ToolKind
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = Field(default=None)
    strict: bool = False


class ToolResult(BaseModel):
    """Sanitized result returned to the model after tool execution."""

    tool_call_id: str
    name: str
    status: ToolResultStatus
    content: str | None = None
    structured: Any | None = None
    error: dict[str, Any] | None = Field(default=None)
    artifact_ids: list[str] = Field(default_factory=list)


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
    reasoning: LLMReasoningConfig = Field(default_factory=LLMReasoningConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Provider-neutral response returned by LLMClient.

    ``reasoning_content`` carries optional raw reasoning returned by a
    Provider. It is human-observability evidence: Agent prompt continuation
    still uses each Tool Call's private ``provider_context``, and callers must
    not append this field to model-visible history on its own.
    """

    provider: str
    model: str
    content: str | None = None
    reasoning_content: str | None = Field(default=None, repr=False)
    parsed_json: Any | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int | None = None
    cached_input_tokens: int = Field(default=0, ge=0)
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
    max_tokens: int = 8192
    context_window_tokens: int = 131072
    timeout_seconds: int = 60
    response_format: LLMResponseFormat = "json_schema"
    tool_choice: str = "none"
    reasoning: LLMReasoningConfig = Field(default_factory=LLMReasoningConfig)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_context_capacity(self) -> "LLMModelConfig":
        """Reserve at least one token of the context window for model input."""
        if self.max_tokens >= self.context_window_tokens:
            raise ValueError(
                "max_tokens must be smaller than context_window_tokens"
            )
        return self


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
