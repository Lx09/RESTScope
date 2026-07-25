"""Build bounded OpenInference message attributes without optional imports."""

from __future__ import annotations

import json

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreparedMessageAttributes:
    """Flattened message attributes plus their bounded-size state."""

    attributes: dict[str, str]
    truncated: bool
    omitted_message_count: int


def prepare_message_attributes(
    messages: list[Any],
    *,
    direction: str,
    max_value_bytes: int,
) -> PreparedMessageAttributes:
    """Flatten messages using the OpenInference indexed attribute convention."""

    grouped = [
        _message_attribute_group(message, direction=direction, index=index)
        for index, message in enumerate(messages)
    ]
    full_attributes = {
        key: value
        for identities, flexible in grouped
        for key, value in (*identities, *flexible)
    }
    if _attribute_value_size(full_attributes) <= max_value_bytes:
        return PreparedMessageAttributes(
            attributes=full_attributes,
            truncated=False,
            omitted_message_count=0,
        )

    attributes: dict[str, str] = {}
    flexible_fields: list[tuple[str, str]] = []
    omitted_message_count = 0
    used_bytes = 0
    for message_index, (identities, flexible) in enumerate(grouped):
        role_key, role_value = identities[0]
        role_size = len(role_value.encode("utf-8"))
        if used_bytes + role_size > max_value_bytes:
            omitted_message_count = len(grouped) - message_index
            break
        attributes[role_key] = role_value
        used_bytes += role_size
        for key, value in identities[1:]:
            value_size = len(value.encode("utf-8"))
            if used_bytes + value_size <= max_value_bytes:
                attributes[key] = value
                used_bytes += value_size
        flexible_fields.extend(flexible)

    remaining_bytes = max(0, max_value_bytes - used_bytes)
    for index, (key, value) in enumerate(flexible_fields):
        fields_left = len(flexible_fields) - index
        field_budget = remaining_bytes // fields_left if fields_left else 0
        bounded_value = _utf8_prefix(value, field_budget)
        attributes[key] = bounded_value
        consumed = len(bounded_value.encode("utf-8"))
        remaining_bytes -= consumed

    return PreparedMessageAttributes(
        attributes=attributes,
        truncated=True,
        omitted_message_count=omitted_message_count,
    )


def _message_attribute_group(
    message: Any,
    *,
    direction: str,
    index: int,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    value = message if isinstance(message, dict) else {}
    prefix = f"llm.{direction}_messages.{index}.message"
    identities = [(f"{prefix}.role", str(value.get("role", "")))]
    flexible: list[tuple[str, str]] = []

    if value.get("name") is not None:
        identities.append((f"{prefix}.name", str(value["name"])))
    if value.get("tool_call_id") is not None:
        identities.append((f"{prefix}.tool_call_id", str(value["tool_call_id"])))
    if value.get("content") is not None:
        flexible.append((f"{prefix}.content", str(value["content"])))

    tool_calls = value.get("tool_calls")
    if not isinstance(tool_calls, list):
        return identities, flexible
    for tool_index, tool_call in enumerate(tool_calls):
        if not isinstance(tool_call, dict):
            continue
        tool_prefix = f"{prefix}.tool_calls.{tool_index}.tool_call"
        if tool_call.get("id") is not None:
            identities.append((f"{tool_prefix}.id", str(tool_call["id"])))
        if tool_call.get("name") is not None:
            identities.append(
                (f"{tool_prefix}.function.name", str(tool_call["name"]))
            )
        if tool_call.get("arguments") is not None:
            flexible.append(
                (
                    f"{tool_prefix}.function.arguments",
                    json.dumps(
                        tool_call["arguments"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                )
            )
    return identities, flexible


def _attribute_value_size(attributes: dict[str, str]) -> int:
    return sum(len(value.encode("utf-8")) for value in attributes.values())


def _utf8_prefix(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    if max_bytes <= 0:
        return ""
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
