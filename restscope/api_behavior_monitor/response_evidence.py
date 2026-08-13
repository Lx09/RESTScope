"""Decode one target response for every downstream Monitor module.

Operation matching happens before this module runs. It normalizes response
headers and media type, retains exact body bytes, and decodes a declared JSON
body once. Observation persistence and the current Contract and Resource
Monitors reuse the resulting :class:`ResponseEvidence`; Bug Oracle needs only
the HTTP status and Generator validity and does not inspect the body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from restscope.data_types import JSONValue
from restscope.target_api.media_type import is_json_media_type, normalize_media_type


@dataclass(frozen=True, slots=True)
class ResponseEvidence:
    """Hold normalized response facts and the sole decoded body representation."""

    status_code: int
    media_type: str | None
    headers: dict[str, str]
    body: bytes
    body_kind: Literal["json", "invalid_json", "text", "binary", "empty"]
    json_value: JSONValue | None = None


def decode_response_evidence(
    *,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
) -> ResponseEvidence:
    """Normalize headers and decode a declared JSON body exactly once."""

    normalized_headers = {name.lower(): value for name, value in headers.items()}
    media_type = normalize_media_type(normalized_headers.get("content-type"))
    if not body:
        return ResponseEvidence(
            status_code=status_code,
            media_type=media_type,
            headers=normalized_headers,
            body=body,
            body_kind="empty",
        )
    if is_json_media_type(media_type):
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ResponseEvidence(
                status_code=status_code,
                media_type=media_type,
                headers=normalized_headers,
                body=body,
                body_kind="invalid_json",
            )
        return ResponseEvidence(
            status_code=status_code,
            media_type=media_type,
            headers=normalized_headers,
            body=body,
            body_kind="json",
            json_value=value,
        )
    body_kind: Literal["text", "binary"] = (
        "text" if media_type is not None and media_type.startswith("text/") else "binary"
    )
    return ResponseEvidence(
        status_code=status_code,
        media_type=media_type,
        headers=normalized_headers,
        body=body,
        body_kind=body_kind,
    )
