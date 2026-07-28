"""Protect the evidence-priority rules shared by all Smoke LLM roles."""

from __future__ import annotations

from restscope.agent.prompt_context import (
    fit_message_context,
    fit_prompt_context,
)
from restscope.llm import LLMMessage, LLMModelConfig, ToolCall


def _model(*, context: int, output: int) -> LLMModelConfig:
    """Build a deliberately small model window for deterministic unit tests."""
    return LLMModelConfig(
        role="test",
        provider="test",
        model="test",
        context_window_tokens=context,
        max_tokens=output,
    )


def test_context_budget_is_context_minus_output_and_reserve() -> None:
    """The prompt allowance must leave both output space and a safety reserve."""
    fitted = fit_prompt_context(
        required={"todo": {"failure": "bad request"}},
        history=[],
        model=_model(context=5000, output=1000),
    )

    assert fitted.input_budget_tokens == 1952
    assert fitted.payload["todo"]["failure"] == "bad request"


def test_recent_history_is_loaded_before_older_history() -> None:
    """When history does not fit, recent exact records survive before old ones."""
    history = [
        {"round": 1, "detail": "old-" + "x" * 1200},
        {"round": 2, "detail": "middle-" + "y" * 1200},
        {"round": 3, "detail": "recent-" + "z" * 1200},
    ]

    fitted = fit_prompt_context(
        required={"todo": {"failure": "current"}},
        history=history,
        model=_model(context=3600, output=800),
    )

    exact_rounds = [
        entry["round"]
        for entry in fitted.payload["history"]
        if "round" in entry
    ]
    assert exact_rounds
    assert exact_rounds[0] == 3
    assert fitted.summarized_history_entries > 0


def test_oversized_current_response_keeps_structure_size_head_and_tail() -> None:
    """Even an oversized required response remains recognizable and auditable."""
    body = "HEAD-" + "a" * 9000 + "-TAIL"
    fitted = fit_prompt_context(
        required={
            "todo": {"failure": "current"},
            "latest_batch": {
                "cases": [
                    {
                        "response": {
                            "status_code": 500,
                            "body": body,
                        }
                    }
                ]
            },
        },
        history=[],
        model=_model(context=3300, output=700),
    )

    response = fitted.payload["latest_batch"]["cases"][0]["response"]
    clipped = response["body"]
    assert response["status_code"] == 500
    assert clipped["context_truncated"] is True
    assert clipped["original_size"] == len(body)
    assert clipped["head"].startswith("HEAD-")
    assert clipped["tail"].endswith("-TAIL")
    assert fitted.truncated_required_values > 0


def test_growing_conversation_keeps_tool_call_and_result_together() -> None:
    """Old groups are summarized while a recent oversized HTTP group stays valid."""
    model = _model(context=7000, output=1000)
    messages = [
        LLMMessage(role="system", content="Investigate one failure."),
        LLMMessage(
            role="user",
            content=(
                '{"todo":{"failure":"current"},"latest_batch":{"cases":[]},'
                '"history":[{"round":1,"detail":"'
                + "h" * 6000
                + '"}]}'
            ),
        ),
        *[
            message
            for index in range(8)
            for message in (
                    LLMMessage(
                        role="assistant",
                        content=(
                            '{"invalid":"old-'
                            + str(index)
                            + "-"
                            + "q" * 2000
                            + '"}'
                        ),
                ),
                LLMMessage(
                    role="user",
                    content="Correct old output " + str(index) + ".",
                ),
            )
        ],
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="restscope.http.request",
                    arguments={"method": "GET", "path": "/projects/known"},
                )
            ],
        ),
        LLMMessage(
            role="tool",
            name="restscope.http.request",
            tool_call_id="call-1",
            content='{"body":"' + "x" * 16000 + '"}',
        ),
    ]

    fitted = fit_message_context(messages, model=model)

    assert fitted.estimated_tokens <= fitted.input_budget_tokens
    assistant = next(
        message
        for message in fitted.messages
        if message.tool_calls
    )
    tool = next(
        message
        for message in fitted.messages
        if message.role == "tool"
    )
    assert assistant.tool_calls[0].id == tool.tool_call_id
    assert "context_truncated" in tool.content
    assert fitted.summarized_conversation_groups >= 1
