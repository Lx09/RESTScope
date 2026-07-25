"""Serialize bounded trace content through the shared Redactor."""

from __future__ import annotations

import json

from dataclasses import dataclass
from typing import Any

from restscope.redaction import Redactor


@dataclass(frozen=True)
class PreparedContent:
    """One serialized trace value plus its size metadata."""

    value: str
    original_size_bytes: int
    truncated: bool


@dataclass
class TraceContentEncoder:
    """Apply central redaction, JSON encoding, and the trace size limit."""

    redactor: Redactor
    max_content_bytes: int = 65536

    def prepare(self, value: Any) -> PreparedContent:
        normalized = self.redactor.redact(value)
        original_size = len(_compact_json(normalized).encode("utf-8"))
        formatted = _formatted_json(normalized)
        if len(formatted.encode("utf-8")) <= self.max_content_bytes:
            return PreparedContent(
                value=formatted,
                original_size_bytes=original_size,
                truncated=False,
            )
        return PreparedContent(
            value=self._truncated_value(normalized),
            original_size_bytes=original_size,
            truncated=True,
        )

    def _truncated_value(self, value: Any) -> str:
        string_limit = max(16, self.max_content_bytes // 2)
        for item_limit in (20, 10, 5, 2, 1, 0):
            current_string_limit = string_limit
            while current_string_limit >= 0:
                payload = {
                    "preview": _structured_preview(
                        value,
                        item_limit=item_limit,
                        string_limit=current_string_limit,
                        depth_limit=6,
                    ),
                    "truncated": True,
                }
                serialized = _formatted_json(payload)
                if len(serialized.encode("utf-8")) <= self.max_content_bytes:
                    return serialized
                if current_string_limit == 0:
                    break
                current_string_limit //= 2
        fallback = _formatted_json({"truncated": True})
        if len(fallback.encode("utf-8")) <= self.max_content_bytes:
            return fallback
        if self.max_content_bytes >= 2:
            return "{}"
        return ""


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _formatted_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _structured_preview(
    value: Any,
    *,
    item_limit: int,
    string_limit: int,
    depth_limit: int,
) -> Any:
    if isinstance(value, str):
        return _utf8_prefix(value, string_limit)
    if depth_limit <= 0:
        if isinstance(value, dict):
            return {}
        if isinstance(value, list):
            return []
        return value
    if isinstance(value, dict):
        preview: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= item_limit:
                break
            preview[_utf8_prefix(str(key), string_limit)] = _structured_preview(
                item,
                item_limit=item_limit,
                string_limit=string_limit,
                depth_limit=depth_limit - 1,
            )
        return preview
    if isinstance(value, list):
        return [
            _structured_preview(
                item,
                item_limit=item_limit,
                string_limit=string_limit,
                depth_limit=depth_limit - 1,
            )
            for item in value[:item_limit]
        ]
    return value


def _utf8_prefix(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    if max_bytes <= 0:
        return ""
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
