"""Describe bounded target responses and their optional behavior observation.

Callers attach an OpenAPI operation context to a request. The Client turns
the bounded response into :class:`TargetResponseObservation` and offers it to
one processor, typically the API Behavior Monitor. The processor is advisory:
its warnings accompany the real HTTP result and never replace it.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from restscope.data_types import JSONObject, JSONValue

from .media_type import is_json_media_type, normalize_media_type
from .request import PreparedTargetRequest, is_sensitive_header


@dataclass(slots=True, frozen=True)
class TargetResponseOperationContext:
    """Attach OpenAPI identity needed to interpret one target response."""

    ir: object
    operation_key: str | None = None
    operation_method: str | None = None
    operation_path: str | None = None
    abstract_test_case_id: str | None = None
    batch_id: str | None = None
    batch_case_index: int | None = None
    input_validity: Literal["valid", "invalid"] | None = None
    replay_directive: TargetReplayDirective | None = None


@dataclass(slots=True, frozen=True)
class TargetReplayDirective:
    """Carry one opaque processor-owned decision through a same-request Replay."""

    primary_observation_id: str | None
    state: object


@dataclass(slots=True, frozen=True)
class TargetResponseObservation:
    """Carry one completed response and its actual persisted request view."""

    method: str
    path: str
    url: str
    status_code: int
    reason_phrase: str
    headers: Mapping[str, str]
    body: bytes
    body_truncated: bool
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    request_json: JSONObject = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TargetTransportObservation:
    """Carry one attempted request that ended before an HTTP response existed."""

    method: str
    path: str
    url: str
    code: str
    message: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    request_json: JSONObject = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorWarning:
    """Describe a processor failure without replacing the HTTP result."""

    code: str
    message: str
    issues: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class TargetResponseProcessorResult:
    """Carry the bounded outcome from a synchronous response processor."""

    response_validation: Literal["evaluated", "partial", "not_evaluated"]
    warnings: tuple[TargetResponseProcessorWarning, ...] = ()
    details: Mapping[str, object] | None = None
    replay_directive: TargetReplayDirective | None = None


class TargetResponseProcessor(Protocol):
    """Allow the Client to notify a monitor without owning that monitor."""

    def process(
        self,
        observation: TargetResponseObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult | TargetResponseProcessorWarning | None:
        """Inspect one bounded response and return advisory structured output."""

        ...

    def process_transport(
        self,
        observation: TargetTransportObservation,
        context: TargetResponseOperationContext,
    ) -> TargetResponseProcessorResult | TargetResponseProcessorWarning | None:
        """Persist one request attempt that produced no HTTP response."""

        ...


@dataclass(slots=True, frozen=True)
class BufferedTargetResponse:
    """Return response metadata and an optional body after the stream closes."""

    status_code: int
    reason_phrase: str
    url: str
    headers: Mapping[str, str]
    encoding: str | None
    body: bytes | None
    body_truncated: bool
    processor_result: TargetResponseProcessorResult | None


def _observation_request_json(
    prepared: PreparedTargetRequest,
    *,
    request_kwargs: Mapping[str, object] | None,
) -> JSONObject:
    """Build the persisted request view without secret-bearing headers."""

    headers = {
        name.lower(): value
        for name, value in prepared.headers.items()
        if not is_sensitive_header(name)
    }
    output: JSONObject = {
        "path": prepared.path,
        "query": [[name, value] for name, value in prepared.url.params.multi_items()],
        "headers": headers,
    }
    body = _observation_request_body(
        request_kwargs or {},
        media_type=_request_media_type(prepared.headers),
    )
    if body is not None:
        output["body"] = body
    return output


def _observation_request_body(
    request_kwargs: Mapping[str, object],
    *,
    media_type: str | None,
) -> JSONObject | None:
    """Encode one caller body into JSON, text, or Base64 evidence."""

    if "json" in request_kwargs:
        return {
            "media_type": media_type or "application/json",
            "encoding": "json",
            "value": _validated_json_value(request_kwargs["json"]),
        }
    if "content" not in request_kwargs:
        return None
    content = request_kwargs["content"]
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        return None
    if is_json_media_type(media_type):
        try:
            value = _validated_json_value(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        else:
            return {
                "media_type": media_type or "application/json",
                "encoding": "json",
                "value": value,
            }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "media_type": media_type or "application/octet-stream",
            "encoding": "base64",
            "value": base64.b64encode(raw).decode("ascii"),
        }
    return {
        "media_type": media_type or "text/plain",
        "encoding": "text",
        "value": text,
    }


def _validated_json_value(value: object) -> JSONValue:
    """Detach and validate one opaque caller value through standard JSON."""

    return json.loads(json.dumps(value, ensure_ascii=False))


def _request_media_type(headers: Mapping[str, str]) -> str | None:
    """Return a normalized request Content-Type without parameters."""

    value = next(
        (item for name, item in headers.items() if name.lower() == "content-type"),
        None,
    )
    return normalize_media_type(value)
