"""Build provider-neutral LLM requests from context packages."""

from __future__ import annotations

from typing import Any

from restscope.llm.schemas import LLMMessage, LLMModelConfig, LLMRequest, LLMResponse, ToolResult, ToolSpec


class LLMRequestFactory:
    """Translate context packages and tool results into LLM requests."""

    def from_context(
        self,
        *,
        context_package,
        model_config: LLMModelConfig,
        output_model: Any | None = None,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
    ) -> LLMRequest:
        json_schema = context_package.output_contract.json_schema
        json_schema_name = context_package.output_contract.name
        if output_model is not None:
            json_schema = output_model.model_json_schema()
            json_schema_name = output_model.__name__

        return LLMRequest(
            provider=model_config.provider,
            model=model_config.model,
            messages=[
                LLMMessage(role=message.role, content=message.content)
                for message in context_package.messages
            ],
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
            timeout_seconds=model_config.timeout_seconds,
            response_format=model_config.response_format,
            json_schema=json_schema,
            json_schema_name=json_schema_name,
            tools=tools or [],
            tool_choice=tool_choice or model_config.tool_choice,
            reasoning=model_config.reasoning,
            metadata={
                "task_id": context_package.task_id,
                "schema_id": context_package.schema_id,
                "role": context_package.role,
                "context_id": context_package.id,
                "context_snapshot_id": context_package.metadata.get("context_snapshot_id"),
                "prompt_version": context_package.prompt_version,
            },
        )

    def with_tool_results(
        self,
        *,
        original_request: LLMRequest,
        original_response: LLMResponse,
        tool_results: list[ToolResult],
    ) -> LLMRequest:
        messages = list(original_request.messages)
        if original_response.content is not None or original_response.tool_calls:
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=original_response.content or "",
                    tool_calls=original_response.tool_calls,
                )
            )

        for result in tool_results:
            content = result.content or str(result.structured or result.error or "")
            messages.append(
                LLMMessage(
                    role="tool",
                    name=result.name,
                    tool_call_id=result.tool_call_id,
                    content=content,
                )
            )

        return original_request.model_copy(update={"messages": messages, "tool_choice": "none"})
