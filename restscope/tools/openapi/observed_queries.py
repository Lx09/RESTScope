"""Match requested field names to retained scalar response observations.

This is the only OpenAPI query behavior that combines current IR structure with
API Behavior Monitor evidence. It returns provenance-free contract locations,
never stored values, timestamps, or database identities.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol

from restscope.api_behavior_monitor.catalog import (
    ObservationRecord,
    ObservedResponseCoordinate,
)
from restscope.openapi_parser.ir import OpenAPISpecIR
from restscope.operation_references import ResponseFieldReference
from restscope.tools.runtime import ToolFailure

from .traversal import (
    _DEFAULT_LIST_LIMIT,
    _field_similarity,
    _normalized_name,
    _observed_media_schema,
    _ObservedFieldMatch,
    _scalar_schema,
    _schema_entries,
    _select_response,
)

_OBSERVATION_PAGE_SIZE = 8


class ObservedResponseReader(Protocol):
    """Expose bounded response facts needed by observed OpenAPI lookup."""

    def list_observed_response_coordinates(
        self,
    ) -> list[ObservedResponseCoordinate]: ...

    def list_observations(
        self,
        *,
        operation_id: str,
        status_code: int | None = None,
        media_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[ObservationRecord]: ...


def find_observed_response_fields(
    *,
    ir_provider: Callable[[], OpenAPISpecIR],
    observed_response_reader: ObservedResponseReader | None,
    name: str,
    offset: int = 0,
    limit: int = _DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """Find similarly named fields backed by retained scalar observations.

    Args:
        name: Natural field name or nested property path to compare after
            deterministic camel/snake/kebab normalization.
        offset: Number of ranked field matches to skip before grouping.
        limit: Maximum field matches in the returned page.

    Returns:
        One structured result grouped by OpenAPI response contract. The
        total and offsets count fields, not response groups. Stored scalar
        values, Schemas, timestamps, and database keys are never returned.

    Raises:
        ToolFailure: No observation Catalog was injected or ``name`` has no
            alphanumeric identifier content.
    """
    if observed_response_reader is None:
        raise ToolFailure(
            code="openapi_observation_catalog_unavailable",
            message="Observed response-field evidence is unavailable.",
        )
    normalized_name = _normalized_name(name)
    if not normalized_name:
        raise ToolFailure(
            code="openapi_response_field_name_invalid",
            message="Response field name must contain letters or numbers.",
        )
    ir = ir_provider()
    candidates: dict[
        tuple[str, int, str, str, str],
        _ObservedFieldMatch,
    ] = {}
    references: dict[
        tuple[str, int, str, str, str],
        ResponseFieldReference,
    ] = {}
    for observed in observed_response_reader.list_observed_response_coordinates():
        operation = ir.operations.get(observed.operation_key)
        if operation is None:
            continue
        try:
            matched_status, response = _select_response(
                operation,
                requested_status=str(observed.status_code),
            )
        except ToolFailure:
            continue
        selected = _observed_media_schema(
            response.contents,
            observed_media_type=observed.media_type,
        )
        if selected is None:
            continue
        selected_media_type, schema = selected
        entries = _schema_entries(
            schema,
            name="body",
            required=False,
            location="body",
            media_type=selected_media_type,
            skip_write_only=True,
            reference=ResponseFieldReference.body(),
        )
        for entry in entries:
            reference = entry.reference
            if (
                not isinstance(reference, ResponseFieldReference)
                or not _scalar_schema(entry.schema)
            ):
                continue
            similarity = _field_similarity(
                normalized_name,
                reference,
            )
            if similarity is None:
                continue
            score, basis = similarity
            key = (
                operation.operation_key,
                observed.status_code,
                matched_status,
                selected_media_type,
                reference.handle,
            )
            candidate = _ObservedFieldMatch(
                operation_key=operation.operation_key,
                status_code=observed.status_code,
                matched_status_code=matched_status,
                media_type=selected_media_type,
                field=reference.handle,
                similarity_score=score,
                match_basis=basis,
            )
            current = candidates.get(key)
            if current is None or candidate.similarity_score > current.similarity_score:
                candidates[key] = candidate
                references[key] = reference

    observed_keys = _observed_candidate_keys(
        observed_response_reader,
        candidates=candidates,
        references=references,
    )

    ranked = sorted(
        (candidates[key] for key in observed_keys),
        key=lambda item: (
            -item.similarity_score,
            item.operation_key,
            item.status_code,
            item.matched_status_code,
            item.media_type,
            item.field,
        ),
    )
    page = ranked[offset : offset + limit]
    groups: dict[tuple[str, int, str, str], dict[str, object]] = {}
    for item in page:
        group_key = (
            item.operation_key,
            item.status_code,
            item.matched_status_code,
            item.media_type,
        )
        group = groups.setdefault(
            group_key,
            {
                "operation_key": item.operation_key,
                "status_code": item.status_code,
                "matched_status_code": item.matched_status_code,
                "media_type": item.media_type,
                "fields": [],
            },
        )
        group["fields"].append(
            {
                "field": item.field,
                "similarity_score": item.similarity_score,
                "match_basis": item.match_basis,
            }
        )
    result: dict[str, object] = {
        "requested_name": name,
        "responses": list(groups.values()),
        "total": len(ranked),
        "offset": offset,
    }
    next_offset = offset + len(page)
    if next_offset < len(ranked):
        result["next_offset"] = next_offset
    return {"structured": result}


def _observed_candidate_keys(
    reader: ObservedResponseReader,
    *,
    candidates: dict[tuple[str, int, str, str, str], _ObservedFieldMatch],
    references: dict[tuple[str, int, str, str, str], ResponseFieldReference],
) -> set[tuple[str, int, str, str, str]]:
    """Confirm OpenAPI-first candidates against exact retained observations.

    Each page contains at most eight full responses. A coordinate stops reading
    as soon as every similarly named candidate selector has produced one
    non-null scalar value, or after its retained one-hundred-row window ends.
    """

    by_coordinate: dict[
        tuple[str, int, str],
        set[tuple[str, int, str, str, str]],
    ] = {}
    for key, candidate in candidates.items():
        coordinate = (
            candidate.operation_key,
            candidate.status_code,
            candidate.media_type,
        )
        by_coordinate.setdefault(coordinate, set()).add(key)

    observed: set[tuple[str, int, str, str, str]] = set()
    for (operation_key, status_code, media_type), keys in by_coordinate.items():
        remaining = set(keys)
        offset = 0
        while remaining and offset < 100:
            observations = reader.list_observations(
                operation_id=operation_key,
                status_code=status_code,
                media_type=media_type,
                offset=offset,
                limit=_OBSERVATION_PAGE_SIZE,
            )
            if not observations:
                break
            for observation in observations:
                document = json.loads(observation.response_json)
                found = {
                    key
                    for key in remaining
                    if any(
                        _is_observed_scalar(value)
                        for value in references[key].select_values(document)
                    )
                }
                observed.update(found)
                remaining.difference_update(found)
                if not remaining:
                    break
            offset += len(observations)
    return observed


def _is_observed_scalar(value: object) -> bool:
    """Return whether actual response evidence can back a scalar Generator."""

    return value is not None and isinstance(value, (str, int, float, bool))
