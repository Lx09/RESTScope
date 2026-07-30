"""Manage one bounded LLM conversation without knowing its domain.

An Agent constructs one :class:`AgentContext` from a system contract and a
compact initial user message.  As the model calls tools or receives validation
feedback, the Agent appends whole interaction groups.  Before each provider
call, this module keeps the newest complete groups and summarizes omitted
history without splitting an assistant tool call from its result.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json

from restscope.llm import LLMMessage, LLMModelConfig, LLMResponse


@dataclass(frozen=True)
class ContextLimits:
    """Declare explicit character and output reserves for one Agent role.

    Args:
        system_chars: Maximum system-instruction characters.
        initial_user_chars: Maximum initial task/evidence characters.
        feedback_chars: Maximum characters in one tool result or correction.
        conversation_chars: Maximum characters across the projected messages.
        required_output_tokens: Tokens reserved for the model's answer.
    """

    system_chars: int
    initial_user_chars: int
    feedback_chars: int
    conversation_chars: int
    required_output_tokens: int

    def __post_init__(self) -> None:
        """Reject unusable budgets at the public boundary."""
        for name, value in self.__dict__.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True)
class ContextMetrics:
    """Expose numeric prompt-shaping facts suitable for trace attributes."""

    original_system_chars: int = 0
    final_system_chars: int = 0
    original_user_chars: int = 0
    final_user_chars: int = 0
    conversation_chars: int = 0
    required_record_count: int = 0
    optional_record_count: int = 0
    clipped_value_count: int = 0
    aggregated_history_count: int = 0
    omitted_history_count: int = 0
    tool_feedback_count: int = 0
    conversation_group_count: int = 0

    def trace_attributes(self, *, prefix: str = "restscope.context") -> dict[str, int]:
        """Return only numeric metrics, never the underlying sensitive text."""
        return {
            f"{prefix}.{name}": value
            for name, value in self.__dict__.items()
        }


class AgentContext:
    """Own the bounded message history for one direct LLM decision session."""

    def __init__(
        self,
        *,
        system: str,
        user: str,
        limits: ContextLimits,
        metrics: ContextMetrics | None = None,
    ) -> None:
        """Create the immutable initial task and an empty interaction ledger.

        ``metrics`` normally comes from :class:`CompactTextWriter.render`. Its
        record/clipping counts are combined with this session's message sizes.
        The original text is retained only in this short-lived object.
        """
        self.limits = limits
        self._system = _clip_text(system, limits.system_chars)
        self._user = _clip_text(user, limits.initial_user_chars)
        self._groups: list[list[LLMMessage]] = []
        initial = metrics or ContextMetrics()
        self._metrics = replace(
            initial,
            original_system_chars=len(system),
            final_system_chars=len(self._system),
            original_user_chars=len(user),
            final_user_chars=len(self._user),
        )

    @property
    def metrics(self) -> ContextMetrics:
        """Return the latest numeric projection metrics."""
        return self._metrics

    def append_assistant(self, response: LLMResponse) -> None:
        """Append one complete model output as the start of a new message group.

        Provider tool arguments and the model's own structured output are JSON
        protocol exceptions. Runtime-generated evidence is still added only
        through ``append_tool_result`` or ``append_feedback`` as compact text.
        """
        if response.content is not None:
            content = response.content
        elif response.parsed_json is not None:
            content = json.dumps(
                response.parsed_json,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        else:
            content = ""
        self._groups.append(
            [
                LLMMessage(
                    role="assistant",
                    content=content,
                    tool_calls=list(response.tool_calls),
                )
            ]
        )
        self._refresh_group_metrics()

    def append_tool_result(self, name: str, call_id: str, text: str) -> None:
        """Attach a compact tool result to the matching latest assistant call.

        A missing call is a programming error: emitting an orphan provider tool
        result would create an invalid conversation and hide a runtime bug.
        """
        target: list[LLMMessage] | None = None
        for group in reversed(self._groups):
            if any(call.id == call_id for message in group for call in message.tool_calls):
                target = group
                break
        if target is None:
            raise ValueError(f"unknown assistant tool call id: {call_id}")
        target.append(
            LLMMessage(
                role="tool",
                name=name,
                tool_call_id=call_id,
                content=_clip_text(text, self.limits.feedback_chars),
            )
        )
        self._metrics = replace(
            self._metrics,
            tool_feedback_count=self._metrics.tool_feedback_count + 1,
        )
        self._refresh_group_metrics()

    def append_feedback(self, text: str) -> None:
        """Append one runtime validation correction as its own recent group."""
        self._groups.append(
            [
                LLMMessage(
                    role="user",
                    content=_clip_text(text, self.limits.feedback_chars),
                )
            ]
        )
        self._refresh_group_metrics()

    def messages_for_request(self, model: LLMModelConfig) -> list[LLMMessage]:
        """Project the newest complete interaction groups into the model window.

        The character budget is the smaller of the role-specific allowance and
        the provider model's remaining input capacity after reserving output
        tokens. The initial system/task pair, newest complete tool group, and
        newest validation feedback remain. Older complete groups are replaced
        with a count-only marker when necessary.
        """
        model_chars = (
            model.context_window_tokens
            - max(model.max_tokens, self.limits.required_output_tokens)
        ) * 4
        if model_chars <= 0:
            raise ValueError("model context window leaves no room for input")
        budget = min(self.limits.conversation_chars, model_chars)

        base = [
            LLMMessage(role="system", content=self._system),
            LLMMessage(role="user", content=self._user),
        ]
        required_indexes = _required_group_indexes(self._groups)
        selected_indexes = set(required_indexes)

        # Fill remaining space from newest to oldest. Required groups are added
        # first even when their text later needs visible clipping, because an
        # orphaned tool call or missing latest correction changes semantics.
        for index in range(len(self._groups) - 1, -1, -1):
            if index in selected_indexes:
                continue
            candidate_indexes = sorted({*selected_indexes, index})
            candidate = _project_messages(
                base,
                self._groups,
                candidate_indexes,
                omitted=len(self._groups) - len(candidate_indexes),
            )
            if _message_chars(candidate) <= budget:
                selected_indexes.add(index)

        omitted = len(self._groups) - len(selected_indexes)
        output = _project_messages(
            base,
            self._groups,
            sorted(selected_indexes),
            omitted=omitted,
        )

        # Required recent groups can themselves be large. Clip message contents
        # in priority order, but retain every message and provider tool-call
        # pairing. Tool metadata is not altered because providers require the
        # original call id, name, and structured arguments.
        output = _fit_message_contents(output, budget)

        self._metrics = replace(
            self._metrics,
            conversation_chars=_message_chars(output),
            aggregated_history_count=omitted,
        )
        return output

    def _refresh_group_metrics(self) -> None:
        """Keep ledger counts current between provider projections."""
        self._metrics = replace(
            self._metrics,
            conversation_group_count=len(self._groups),
        )


def _message_chars(messages: list[LLMMessage]) -> int:
    """Estimate provider input size without serializing runtime evidence."""
    return sum(
        len(message.content)
        + len(message.role)
        + len(message.name or "")
        + len(message.tool_call_id or "")
        + sum(
            len(call.id)
            + len(call.name)
            + len(json.dumps(call.arguments, separators=(",", ":"), default=str))
            for call in message.tool_calls
        )
        for message in messages
    )


def _required_group_indexes(groups: list[list[LLMMessage]]) -> set[int]:
    """Locate the newest tool exchange and newest validation-feedback group."""
    required: set[int] = set()
    for index in range(len(groups) - 1, -1, -1):
        if any(message.tool_calls or message.role == "tool" for message in groups[index]):
            required.add(index)
            break
    for index in range(len(groups) - 1, -1, -1):
        if any(message.role == "user" for message in groups[index]):
            required.add(index)
            break
    return required


def _project_messages(
    base: list[LLMMessage],
    groups: list[list[LLMMessage]],
    selected_indexes: list[int],
    *,
    omitted: int,
) -> list[LLMMessage]:
    """Build one chronological projection and insert a count-only gap marker."""
    output = list(base)
    if omitted:
        output.append(
            LLMMessage(
                role="user",
                content=f"OLDER INTERACTIONS SUMMARIZED | groups={omitted}",
            )
        )
    output.extend(
        message
        for index in selected_indexes
        for message in groups[index]
    )
    return output


def _fit_message_contents(
    messages: list[LLMMessage],
    budget: int,
) -> list[LLMMessage]:
    """Clip text while preserving the complete projected message structure."""
    output = list(messages)
    if _message_chars(output) <= budget:
        return output

    # Old runtime feedback is least valuable, followed by the initial task and
    # system contract. Reverse order is avoided here because the latest tool
    # result and correction are the evidence the next model call needs most.
    clip_order = [
        *range(2, max(2, len(output) - 2)),
        1,
        0,
        *range(max(2, len(output) - 2), len(output)),
    ]
    for index in clip_order:
        overflow = _message_chars(output) - budget
        if overflow <= 0:
            break
        content = output[index].content
        target = max(32, len(content) - overflow)
        if target >= len(content):
            continue
        output[index] = output[index].model_copy(
            update={"content": _clip_text(content, target)}
        )

    if _message_chars(output) > budget:
        raise ValueError(
            "model context window cannot hold required message and tool metadata"
        )
    return output


def _clip_text(text: str, max_chars: int) -> str:
    """Preserve head, tail, and original size inside an exact allowance."""
    if len(text) <= max_chars:
        return text
    marker = f"CLIPPED MESSAGE | original-chars={len(text)}\n"
    if len(marker) >= max_chars:
        return marker[:max_chars]
    remaining = max_chars - len(marker)
    separator = "\n...\n"
    if remaining <= len(separator) + 2:
        return (marker + text[:remaining])[:max_chars]
    evidence = remaining - len(separator)
    head = evidence // 2
    tail = evidence - head
    return marker + text[:head] + separator + text[-tail:]
