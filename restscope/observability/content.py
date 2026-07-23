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
        serialized = json.dumps(
            self.redactor.redact(value),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        original_size = len(serialized.encode("utf-8"))
        if original_size <= self.max_content_bytes:
            return PreparedContent(
                value=serialized,
                original_size_bytes=original_size,
                truncated=False,
            )
        return PreparedContent(
            value=self._truncated_value(serialized),
            original_size_bytes=original_size,
            truncated=True,
        )

    def _truncated_value(self, serialized: str) -> str:
        encoded = serialized.encode("utf-8")
        preview_size = min(len(encoded), self.max_content_bytes)
        while preview_size >= 0:
            preview = encoded[:preview_size].decode("utf-8", errors="ignore")
            payload = json.dumps(
                {"preview": preview, "truncated": True},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(payload.encode("utf-8")) <= self.max_content_bytes:
                return payload
            preview_size -= max(1, preview_size // 8)
        return "{}"
