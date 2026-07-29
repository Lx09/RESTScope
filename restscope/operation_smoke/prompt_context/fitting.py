"""Apply the shared token-budget policy to structured Smoke prompt evidence.

The fitter works with JSON-like values rather than role-specific schemas. Its
estimate intentionally errs on the simple and transparent side: four UTF-8
JSON characters count as one token. Provider tokenizers remain the final
authority, but this deterministic estimate gives every role the same reserve
and makes clipping behavior testable without a model call.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any

from restscope.llm import LLMMessage, LLMModelConfig


_PROMPT_RESERVE_TOKENS = 2048
_MIN_EXCERPT_CHARS = 96


@dataclass(frozen=True)
class FittedPromptContext:
    """Describe one fitted payload and any fidelity reduction it required."""

    payload: dict[str, Any]
    input_budget_tokens: int
    estimated_tokens: int
    summarized_history_entries: int
    truncated_required_values: int


@dataclass(frozen=True)
class FittedMessageContext:
    """Describe one model-safe conversation projection."""

    messages: list[LLMMessage]
    input_budget_tokens: int
    estimated_tokens: int
    summarized_conversation_groups: int


def fit_prompt_context(
    *,
    required: dict[str, Any],
    history: list[dict[str, Any]],
    model: LLMModelConfig,
    input_budget_tokens: int | None = None,
) -> FittedPromptContext:
    """Fit required current evidence and newest-first history into the window.

    ``required`` is never replaced by a generic summary. When an individual
    current value makes the prompt too large, its position remains intact and
    the value becomes an explicit object containing original size plus head
    and tail excerpts. Historical records are considered newest first; records
    that do not fit are represented by compact metadata summaries.
    """
    default_budget = (
        model.context_window_tokens
        - model.max_tokens
        - _PROMPT_RESERVE_TOKENS
    )
    budget = (
        default_budget
        if input_budget_tokens is None
        else min(default_budget, input_budget_tokens)
    )
    if budget <= 0:
        raise ValueError(
            "context_window_tokens must exceed max_tokens plus the 2048-token "
            "prompt reserve"
        )

    fitted_required = deepcopy(required)
    truncated = _clip_required_to_budget(fitted_required, budget)
    payload = dict(fitted_required)
    payload["history"] = []

    omitted: list[dict[str, Any]] = []
    # The ledger stores chronological records. Reverse it so the newest exact
    # evidence gets the first opportunity to occupy the limited window.
    for record in reversed(history):
        candidate = deepcopy(payload)
        candidate["history"] = [
            *payload["history"],
            deepcopy(record),
        ]
        if _estimate_tokens(candidate) <= budget:
            payload = candidate
        else:
            omitted.append(record)

    if omitted:
        summary = {
            "older_history_summary": [
                _history_summary(record)
                for record in omitted
            ],
            "summarized_entry_count": len(omitted),
        }
        candidate = deepcopy(payload)
        candidate["history"] = [*payload["history"], summary]
        if _estimate_tokens(candidate) <= budget:
            payload = candidate
        else:
            # Under an extremely small window, preserve the count even when
            # per-record keys cannot fit.
            count_only = {
                "older_history_summary": "details omitted for context budget",
                "summarized_entry_count": len(omitted),
            }
            candidate["history"] = [*payload["history"], count_only]
            if _estimate_tokens(candidate) <= budget:
                payload = candidate

    return FittedPromptContext(
        payload=payload,
        input_budget_tokens=budget,
        estimated_tokens=_estimate_tokens(payload),
        summarized_history_entries=len(omitted),
        truncated_required_values=truncated,
    )


def fit_message_context(
    messages: list[LLMMessage],
    *,
    model: LLMModelConfig,
) -> FittedMessageContext:
    """Fit a growing role conversation while retaining valid tool-call groups.

    The full conversation remains in Agent memory. This projection keeps the
    system instruction, structurally refits the initial evidence JSON, and
    loads complete recent assistant/tool/correction groups newest first. Older
    groups become one explicit summary instead of being silently dropped.
    """
    budget = model.context_window_tokens - model.max_tokens - _PROMPT_RESERVE_TOKENS
    if budget <= 0:
        raise ValueError(
            "context_window_tokens must exceed max_tokens plus the 2048-token "
            "prompt reserve"
        )
    if _estimate_tokens([item.model_dump(mode="json") for item in messages]) <= budget:
        return FittedMessageContext(
            messages=list(messages),
            input_budget_tokens=budget,
            estimated_tokens=_estimate_tokens(
                [item.model_dump(mode="json") for item in messages]
            ),
            summarized_conversation_groups=0,
        )
    if len(messages) < 2:
        clipped = [
            _fit_one_message(message, token_budget=max(32, budget // len(messages)))
            for message in messages
        ]
        return FittedMessageContext(
            messages=clipped,
            input_budget_tokens=budget,
            estimated_tokens=_estimate_tokens(
                [item.model_dump(mode="json") for item in clipped]
            ),
            summarized_conversation_groups=0,
        )

    system = messages[0]
    groups = _conversation_groups(messages[2:])
    # Reserve half the usable prompt for current evidence. The remainder keeps
    # the newest continuous Solve/repair/tool exchanges.
    initial_budget = max(128, budget // 2)
    initial = _fit_initial_user(
        messages[1],
        model=model,
        token_budget=initial_budget,
    )
    selected_groups: list[list[LLMMessage]] = []
    omitted = 0
    newest_first_groups = list(reversed(groups))
    for index, group in enumerate(newest_first_groups):
        candidate_group = _fit_group(
            group,
            token_budget=max(128, budget // 3),
        )
        candidate = [
            system,
            initial,
            *[
                item
                for selected in reversed(selected_groups)
                for item in selected
            ],
            *candidate_group,
        ]
        if _estimate_tokens(
            [item.model_dump(mode="json") for item in candidate]
        ) <= budget:
            selected_groups.append(candidate_group)
        else:
            # Stop here: fitting any older, smaller group instead would violate
            # the explicit recent-history priority.
            omitted += len(newest_first_groups) - index
            break

    output = _project_messages(
        system=system,
        initial=initial,
        selected_groups=selected_groups,
        omitted=omitted,
    )
    estimated = _estimate_tokens(
        [item.model_dump(mode="json") for item in output]
    )
    # The summary itself occupies space. Drop oldest selected groups until the
    # final projection, including that summary, is inside the hard boundary.
    while estimated > budget and selected_groups:
        selected_groups.pop()
        omitted += 1
        output = _project_messages(
            system=system,
            initial=initial,
            selected_groups=selected_groups,
            omitted=omitted,
        )
        estimated = _estimate_tokens(
            [item.model_dump(mode="json") for item in output]
        )
    return FittedMessageContext(
        messages=output,
        input_budget_tokens=budget,
        estimated_tokens=estimated,
        summarized_conversation_groups=omitted,
    )


def _clip_required_to_budget(value: Any, budget: int) -> int:
    """Replace the largest strings until required evidence fits the budget."""
    clipped = 0
    while _estimate_tokens(value) > budget:
        strings = _string_locations(value)
        if not strings:
            break
        parent, key, text = max(strings, key=lambda item: len(item[2]))
        if len(text) <= _MIN_EXCERPT_CHARS * 2:
            break
        # Shrink aggressively enough to leave room for surrounding structure.
        excess_chars = (_estimate_tokens(value) - budget) * 4
        target = max(
            _MIN_EXCERPT_CHARS * 2,
            len(text) - excess_chars - 256,
        )
        excerpt = max(_MIN_EXCERPT_CHARS, target // 2)
        parent[key] = {
            "context_truncated": True,
            "original_size": len(text),
            "head": text[:excerpt],
            "tail": text[-excerpt:],
        }
        clipped += 1
    return clipped


def _string_locations(
    value: Any,
) -> list[tuple[dict[str, Any] | list[Any], str | int, str]]:
    """Return mutable parent/key pairs for every nested string value."""
    found: list[tuple[dict[str, Any] | list[Any], str | int, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                found.append((value, key, child))
            elif isinstance(child, (dict, list)):
                found.extend(_string_locations(child))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                found.append((value, index, child))
            elif isinstance(child, (dict, list)):
                found.extend(_string_locations(child))
    return found


def _history_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Keep useful record identity without repeating its raw evidence."""
    identity_keys = (
        "round",
        "round_number",
        "todo_id",
        "status",
        "outcome",
        "failure",
        "reason",
    )
    summary = {
        key: deepcopy(record[key])
        for key in identity_keys
        if key in record
    }
    summary["original_json_size"] = len(_serialize(record))
    summary["history_summarized"] = True
    return summary


def _estimate_tokens(value: Any) -> int:
    """Estimate serialized prompt tokens with a documented four-char rule."""
    return max(1, math.ceil(len(_serialize(value)) / 4))


def _serialize(value: Any) -> str:
    """Serialize JSON-like evidence consistently for sizing and prompting."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _conversation_groups(
    messages: list[LLMMessage],
) -> list[list[LLMMessage]]:
    """Keep assistant tool calls paired with their tool results and feedback."""
    groups: list[list[LLMMessage]] = []
    for message in messages:
        if message.role == "assistant" or not groups:
            groups.append([message])
        else:
            groups[-1].append(message)
    return groups


def _project_messages(
    *,
    system: LLMMessage,
    initial: LLMMessage,
    selected_groups: list[list[LLMMessage]],
    omitted: int,
) -> list[LLMMessage]:
    """Build one chronological conversation plus an explicit older summary."""
    output = [system, initial]
    if omitted:
        output.append(
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "conversation_history_summarized": True,
                        "summarized_group_count": omitted,
                        "note": (
                            "Older model, correction, and HTTP observation "
                            "groups remain in App memory but were summarized "
                            "for this context window."
                        ),
                    },
                    separators=(",", ":"),
                ),
            )
        )
    output.extend(
        item
        for selected in reversed(selected_groups)
        for item in selected
    )
    return output


def _fit_initial_user(
    message: LLMMessage,
    *,
    model: LLMModelConfig,
    token_budget: int,
) -> LLMMessage:
    """Refit initial structured evidence, preferring its current fields."""
    try:
        payload = json.loads(message.content)
    except (TypeError, json.JSONDecodeError):
        return _fit_one_message(message, token_budget=token_budget)
    if not isinstance(payload, dict):
        return _fit_one_message(message, token_budget=token_budget)
    history = payload.pop("history", [])
    fitted = fit_prompt_context(
        required=payload,
        # Initial role fitting already rendered history newest first. Restore
        # chronological order before applying the same newest-first policy to
        # a smaller growing-conversation allowance.
        history=(
            list(reversed(history))
            if isinstance(history, list)
            else []
        ),
        model=model,
        input_budget_tokens=token_budget,
    )
    return message.model_copy(
        update={
            "content": json.dumps(
                fitted.payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        }
    )


def _fit_group(
    group: list[LLMMessage],
    *,
    token_budget: int,
) -> list[LLMMessage]:
    """Clip oversized current tool/correction values without splitting a group."""
    if _estimate_tokens(
        [item.model_dump(mode="json") for item in group]
    ) <= token_budget:
        return list(group)
    per_message = max(32, token_budget // max(1, len(group)))
    return [
        _fit_one_message(message, token_budget=per_message)
        for message in group
    ]


def _fit_one_message(
    message: LLMMessage,
    *,
    token_budget: int,
) -> LLMMessage:
    """Preserve message metadata while marking an oversized content excerpt."""
    if _estimate_tokens(message.model_dump(mode="json")) <= token_budget:
        return message
    text = message.content
    # Two excerpts plus JSON metadata must fit inside the message allowance.
    # Using one character per token for each side leaves roughly half the
    # four-characters-per-token estimate for that metadata.
    excerpt_chars = max(16, token_budget)
    content = json.dumps(
        {
            "context_truncated": True,
            "original_size": len(text),
            "head": text[:excerpt_chars],
            "tail": text[-excerpt_chars:],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return message.model_copy(update={"content": content})
