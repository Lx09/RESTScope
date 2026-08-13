"""Expose safe, bounded reads of durable Batches and executed Test Cases.

The API Behavior Catalog stores exact request/result evidence. This Tool Module
turns that state into Agent-visible pages: Batch queries reveal only grouped
Observation identities, while a Test Case query returns one complete persisted
record with a bounded response body and redacted sensitive response headers.
Both behaviors are read-only and do not grant themselves to any Agent Profile.
"""

from __future__ import annotations

import base64
import json

from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog, ObservationRecord
from restscope.llm import ToolSpec
from restscope.target_api.request import is_sensitive_header
from restscope.tools.runtime import ToolBinding, ToolFailure


TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME = "test_case.get_batch_results"
TEST_CASE_GET_TOOL_NAME = "test_case.get"
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 200
_BODY_LIMIT_BYTES = 16 * 1024
_MAX_OUTPUT_CHARACTERS = 24_000
_REDACTED = "[REDACTED]"


class TestCaseQueryToolBackend:
    """Read durable Batch and Observation records through the Catalog.

    Args:
        catalog: The App-owned persistence Interface. Constructing this backend
            creates no transaction and grants no Tool permission.
    """

    def __init__(self, *, catalog: APIBehaviorCatalog) -> None:
        """Retain the Catalog for later short read-only transactions."""

        self._catalog = catalog

    def get_batch_results(
        self,
        *,
        batch_id: str,
        offset: int = 0,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, object]:
        """Return one Batch summary and a grouped page of Observation IDs.

        Args:
            batch_id: Exact durable identity returned by ``test_case.run_batch``.
            offset: Number of original Batch cases to skip.
            limit: Maximum cases to read; the Tool Schema caps this at 200.

        Returns:
            A structured ``found`` or ``not_found`` payload. Found pages remain
            ordered by zero-based Batch Case index before grouping.
        """

        batch = self._catalog.get_batch(batch_id)
        if batch is None:
            return {
                "structured": {
                    "status": "not_found",
                    "batch_id": batch_id,
                    "summary": None,
                    "total": 0,
                    "offset": offset,
                    "groups": [],
                }
            }
        observations, total = self._catalog.list_batch_observations(
            batch_id=batch_id,
            offset=offset,
            limit=limit,
        )
        groups: list[dict[str, object]] = []
        group_indexes: dict[tuple[str, str, int | None], int] = {}
        for observation in observations:
            key = (
                observation.operation_id,
                observation.outcome_kind,
                observation.status_code,
            )
            group_index = group_indexes.get(key)
            if group_index is None:
                group_index = len(groups)
                group_indexes[key] = group_index
                groups.append(
                    {
                        "operation_key": observation.operation_id,
                        "outcome_kind": observation.outcome_kind,
                        "status_code": observation.status_code,
                        "observation_ids": [],
                    }
                )
            observation_ids = groups[group_index]["observation_ids"]
            assert isinstance(observation_ids, list)
            observation_ids.append(observation.observation_id)

        result: dict[str, object] = {
            "status": "found",
            "batch_id": batch.batch_id,
            "summary": batch.summary,
            "total": total,
            "offset": offset,
            "groups": groups,
        }
        next_offset = offset + len(observations)
        if next_offset < total:
            result["next_offset"] = next_offset
        return {"structured": _require_bounded_output(result)}

    def get(self, *, test_case_id: str) -> dict[str, object]:
        """Return one complete persisted request and bounded result record.

        Args:
            test_case_id: An Observation ID, which is RESTScope's durable Test
                Case identity for an executed request.

        Returns:
            A structured ``found`` result or ``not_found`` without raising.
            Sensitive response header names remain visible, but their values
            are replaced with ``[REDACTED]``.
        """

        observation = self._catalog.get_observation(test_case_id)
        if observation is None:
            return {
                "structured": {
                    "status": "not_found",
                    "test_case_id": test_case_id,
                    "observation": None,
                }
            }
        result = {
            "status": "found",
            "test_case_id": observation.observation_id,
            "observation": _observation_view(observation),
        }
        return {"structured": _require_bounded_output(result)}


def _observation_view(observation: ObservationRecord) -> dict[str, object]:
    """Project one exact database record through the Agent safety boundary."""

    response_headers = (
        {
            name: _REDACTED if is_sensitive_header(name) else value
            for name, value in observation.response_headers.items()
        }
        if observation.response_headers is not None
        else None
    )
    return {
        "operation_key": observation.operation_id,
        "timestamp": observation.timestamp.isoformat(),
        "abstract_test_case_id": observation.abstract_test_case_id,
        "batch_id": observation.batch_id,
        "batch_case_index": observation.batch_case_index,
        "request": observation.request_json,
        "outcome_kind": observation.outcome_kind,
        "status_code": observation.status_code,
        "reason_phrase": observation.reason_phrase,
        "media_type": observation.media_type,
        "response_headers": response_headers,
        "response_body": _response_body_view(observation),
        "transport_code": observation.transport_code,
        "transport_message": observation.transport_message,
    }


def _response_body_view(observation: ObservationRecord) -> dict[str, object] | None:
    """Return at most 16 KiB of HTTP body in its safest useful representation."""

    body = observation.response_body
    if body is None:
        return None
    prefix = body[:_BODY_LIMIT_BYTES]
    if observation.body_format == "base64":
        value = base64.b64encode(prefix).decode("ascii")
    else:
        # A valid complete UTF-8 body can be cut between multibyte code points.
        # Replacement decoding keeps the projection readable and bounded while
        # the database continues to own the exact original bytes.
        value = prefix.decode("utf-8", errors="replace")
    return {
        "format": observation.body_format,
        "value": value,
        "size_bytes": len(body),
        "truncated": len(body) > len(prefix),
    }


def _require_bounded_output(result: dict[str, object]) -> dict[str, object]:
    """Fail safely if non-body metadata makes a Tool result unexpectedly large."""

    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=False)) > _MAX_OUTPUT_CHARACTERS:
        raise ToolFailure(
            code="test_case_output_too_large",
            message="Test Case query output exceeds 24000 characters",
        )
    return result


def test_case_get_batch_results_tool_spec() -> ToolSpec:
    """Return the global contract for paginated grouped Batch results."""

    return ToolSpec(
        name=TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME,
        description=(
            "Get one durable Batch summary and a stable page of Observation IDs "
            "grouped by operation, outcome kind, and HTTP status code."
        ),
        kind="local_function",
        input_schema=_batch_input_schema(),
        output_schema=_batch_output_schema(),
        strict=True,
    )


def test_case_get_tool_spec() -> ToolSpec:
    """Return the global contract for one exact executed Test Case."""

    return ToolSpec(
        name=TEST_CASE_GET_TOOL_NAME,
        description=(
            "Get one executed Test Case by Observation ID, including its persisted "
            "request and bounded HTTP or transport result."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "test_case_id": {"type": "string", "minLength": 1, "maxLength": 200}
            },
            "required": ["test_case_id"],
            "additionalProperties": False,
        },
        output_schema=_test_case_output_schema(),
        strict=True,
    )


def test_case_query_tool_bindings(
    backend: TestCaseQueryToolBackend,
) -> tuple[ToolBinding, ToolBinding]:
    """Bind both read-only query contracts to one App-owned Catalog backend."""

    return (
        ToolBinding(
            name=TEST_CASE_GET_BATCH_RESULTS_TOOL_NAME,
            execute=backend.get_batch_results,
        ),
        ToolBinding(name=TEST_CASE_GET_TOOL_NAME, execute=backend.get),
    )


def _batch_input_schema() -> dict[str, object]:
    """Describe the bounded Batch pagination input."""

    return {
        "type": "object",
        "properties": {
            "batch_id": {"type": "string", "minLength": 1, "maxLength": 200},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIMIT,
                "default": _DEFAULT_LIMIT,
            },
        },
        "required": ["batch_id"],
        "additionalProperties": False,
    }


def _batch_output_schema() -> dict[str, object]:
    """Describe found and missing Batch results without exposing case bodies."""

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["found", "not_found"]},
            "batch_id": {"type": "string"},
            "summary": {
                "type": ["object", "null"],
                "description": "Complete bounded Batch summary JSON, or null when absent.",
                "additionalProperties": True,
            },
            "total": {"type": "integer", "minimum": 0},
            "offset": {"type": "integer", "minimum": 0},
            "next_offset": {"type": "integer", "minimum": 0},
            "groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "operation_key": {"type": "string"},
                        "outcome_kind": {"type": "string", "enum": ["http", "transport"]},
                        "status_code": {"type": ["integer", "null"], "minimum": 100, "maximum": 599},
                        "observation_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["operation_key", "outcome_kind", "status_code", "observation_ids"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["status", "batch_id", "summary", "total", "offset", "groups"],
        "additionalProperties": False,
    }


def _test_case_output_schema() -> dict[str, object]:
    """Describe one safe Observation projection or a missing identity."""

    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["found", "not_found"]},
            "test_case_id": {"type": "string"},
            "observation": {
                "type": ["object", "null"],
                "properties": {
                    "operation_key": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "abstract_test_case_id": nullable_string,
                    "batch_id": nullable_string,
                    "batch_case_index": {"type": ["integer", "null"], "minimum": 0},
                    "request": {
                        "type": "object",
                        "description": "Complete persisted safe request evidence.",
                        "additionalProperties": True,
                    },
                    "outcome_kind": {"type": "string", "enum": ["http", "transport"]},
                    "status_code": {"type": ["integer", "null"], "minimum": 100, "maximum": 599},
                    "reason_phrase": nullable_string,
                    "media_type": nullable_string,
                    "response_headers": {
                        "type": ["object", "null"],
                        "description": "Response headers with sensitive values redacted.",
                        "additionalProperties": {"type": "string"},
                    },
                    "response_body": {
                        "type": ["object", "null"],
                        "properties": {
                            "format": {"type": "string", "enum": ["json", "text", "base64"]},
                            "value": {"type": "string"},
                            "size_bytes": {"type": "integer", "minimum": 0},
                            "truncated": {"type": "boolean"},
                        },
                        "required": ["format", "value", "size_bytes", "truncated"],
                        "additionalProperties": False,
                    },
                    "transport_code": nullable_string,
                    "transport_message": nullable_string,
                },
                "required": [
                    "operation_key", "timestamp", "abstract_test_case_id", "batch_id",
                    "batch_case_index", "request", "outcome_kind", "status_code",
                    "reason_phrase", "media_type", "response_headers", "response_body",
                    "transport_code", "transport_message",
                ],
                "additionalProperties": False,
            },
        },
        "required": ["status", "test_case_id", "observation"],
        "additionalProperties": False,
    }
