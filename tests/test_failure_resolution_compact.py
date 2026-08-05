"""Protect the local B/H/C/S/H-prime compaction protocol for Resolution."""

from __future__ import annotations

from restscope.context import AgentContext, ContextLimits
from restscope.llm import LLMModelConfig, LLMResponse, ToolCall
from restscope.operation_smoke.output_limit import ModelOutputLimit


class StubClient:
    """Return scripted Compact responses and retain every provider request."""

    def __init__(self, responses):
        """Store responses or exceptions in their provider-call order."""
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Record the request, then return or raise the next scripted value."""
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _model() -> LLMModelConfig:
    """Return the FAST profile selected for one Compact Agent call."""
    return LLMModelConfig(
        role="operation_smoke_failure_resolution_compact",
        provider="stub",
        model="fast-model",
        max_tokens=4_096,
        context_window_tokens=32_768,
    )


def _context() -> AgentContext:
    """Create B and H with one complete DeepSeek-compatible tool exchange."""
    context = AgentContext(
        system="Resolution system B",
        user="Original Failure source U",
        limits=ContextLimits(
            system_chars=1_000,
            initial_user_chars=1_000,
            feedback_chars=1_000,
            conversation_chars=20_000,
            required_output_tokens=4_096,
        ),
    )
    context.append_assistant(
        LLMResponse(
            provider="deepseek",
            model="resolution-model",
            tool_calls=[
                ToolCall(
                    id="read-1",
                    name="failure_resolution.read_worklist",
                    arguments={},
                    provider_context={"reasoning_content": "continuation"},
                )
            ],
        )
    )
    context.append_tool_result(
        "failure_resolution.read_worklist",
        "read-1",
        "revision: 2; active_item_id: E2",
    )
    return context


def test_compact_agent_sends_b_plus_complete_h_plus_temporary_c() -> None:
    """A successful Compact call returns S without mutating the source Context."""
    from restscope.operation_smoke.failure_resolution.compact import (
        COMPACT_INSTRUCTION,
        FailureResolutionCompactAgent,
    )

    compact_summary = "# Checkpoint\n\nE1 is resolved; investigate E2 next."
    client = StubClient(
        [
            LLMResponse(
                provider="stub",
                model="fast-model",
                content=compact_summary,
            )
        ]
    )
    context = _context()
    history_before_compact = context.clone_history()
    output_limit = ModelOutputLimit()

    result = FailureResolutionCompactAgent(
        client=client,
        model=_model(),
    ).run(context=context, output_limit=output_limit)

    assert result == compact_summary
    assert output_limit.used == 1
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.messages[0].content == "Resolution system B"
    assert request.messages[1:-1] == history_before_compact
    assert request.messages[-1].role == "user"
    assert request.messages[-1].content == COMPACT_INSTRUCTION
    assert request.messages[2].tool_calls[0].provider_context == {
        "reasoning_content": "continuation"
    }
    assert request.response_format == "text"
    assert request.json_schema is None
    assert request.tools == []
    assert request.tool_choice == "none"
    assert request.reasoning.mode == "disabled"
    assert request.metadata == {
        "role": "operation_smoke_failure_resolution_compact"
    }
    assert context.clone_history() == history_before_compact
    assert all(
        message.content != COMPACT_INSTRUCTION
        for message in context.clone_history()
    )


def test_compact_agent_retries_one_provider_failure_with_the_same_h_plus_c() -> None:
    """A transient first failure consumes one slot but cannot change H or C."""
    from restscope.llm import ProviderInvokeError
    from restscope.operation_smoke.failure_resolution.compact import (
        FailureResolutionCompactAgent,
    )

    compact_summary = "# Checkpoint\n\nContinue E2."
    client = StubClient(
        [
            ProviderInvokeError("temporary compact failure"),
            LLMResponse(
                provider="stub",
                model="fast-model",
                content=compact_summary,
            ),
        ]
    )
    context = _context()
    history_before_compact = context.clone_history()
    output_limit = ModelOutputLimit()

    result = FailureResolutionCompactAgent(
        client=client,
        model=_model(),
    ).run(context=context, output_limit=output_limit)

    assert result == compact_summary
    assert output_limit.used == 2
    assert len(client.requests) == 2
    assert client.requests[0].messages == client.requests[1].messages
    assert context.clone_history() == history_before_compact


def test_compact_agent_reports_two_empty_summaries_without_replacing_h() -> None:
    """Two blank S responses disable compaction at the session boundary later."""
    import pytest

    from restscope.operation_smoke.failure_resolution.compact import (
        FailureResolutionCompactAgent,
        FailureResolutionCompactError,
    )

    client = StubClient(
        [
            LLMResponse(provider="stub", model="fast-model", content="  "),
            LLMResponse(provider="stub", model="fast-model", content=None),
        ]
    )
    context = _context()
    history_before_compact = context.clone_history()
    output_limit = ModelOutputLimit()

    with pytest.raises(FailureResolutionCompactError, match="two attempts"):
        FailureResolutionCompactAgent(
            client=client,
            model=_model(),
        ).run(context=context, output_limit=output_limit)

    assert output_limit.used == 2
    assert len(client.requests) == 2
    assert context.clone_history() == history_before_compact
