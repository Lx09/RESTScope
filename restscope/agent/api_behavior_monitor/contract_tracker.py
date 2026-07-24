"""Deterministic App-lifetime evolution of observed response contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, Literal

from restscope.openapi_parser import OpenAPISpecIR
from restscope.openapi_parser.ir import MediaTypeIR, ResponseIR, SchemaIR


ContractCheckStatus = Literal[
    "matched",
    "updated",
    "already_checked",
    "pending_retry",
]


class ResponseContractError(ValueError):
    """Stable failure raised when an observation cannot target the current IR."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResponseContractKey:
    operation_key: str
    status_code: int
    media_type: str | None


@dataclass(frozen=True, slots=True)
class ContractCheckResult:
    key: ResponseContractKey
    status: ContractCheckStatus
    changes: tuple[str, ...] = ()


class ResponseContractTracker:
    """Check each exact status/media observation once for one App lifetime."""

    def __init__(self) -> None:
        self._states: dict[ResponseContractKey, Literal["checking", "checked", "pending"]] = {}
        self._lock = RLock()

    def observe(
        self,
        *,
        ir: OpenAPISpecIR,
        operation_key: str,
        status_code: int,
        media_type: str | None,
        body: bytes,
        body_truncated: bool = False,
    ) -> ContractCheckResult:
        normalized_media = normalize_media_type(media_type)
        key = ResponseContractKey(
            operation_key=operation_key,
            status_code=status_code,
            media_type=normalized_media,
        )
        with self._lock:
            if self._states.get(key) == "checked":
                return ContractCheckResult(key=key, status="already_checked")
            operation = ir.operations.get(operation_key)
            if operation is None:
                raise ResponseContractError(
                    "operation_not_found",
                    f"Operation {operation_key!r} is not present in the current OpenAPI IR",
                )
            self._states[key] = "checking"
            observed_schema, body_kind = _observed_body_schema(
                media_type=normalized_media,
                body=body,
                body_truncated=body_truncated,
            )
            if body_kind == "pending":
                self._states[key] = "pending"
                return ContractCheckResult(key=key, status="pending_retry")

            changes = _merge_response(
                operation.responses.by_status,
                status_code=status_code,
                media_type=normalized_media,
                observed_schema=observed_schema,
                body_kind=body_kind,
            )
            self._states[key] = "checked"
            return ContractCheckResult(
                key=key,
                status="updated" if changes else "matched",
                changes=tuple(changes),
            )


def normalize_media_type(media_type: str | None) -> str | None:
    if media_type is None:
        return None
    normalized = media_type.split(";", 1)[0].strip().lower()
    return normalized or None


def _observed_body_schema(
    *,
    media_type: str | None,
    body: bytes,
    body_truncated: bool,
) -> tuple[SchemaIR | None, Literal["json", "text", "empty", "binary", "pending"]]:
    if not body:
        return None, "empty"
    if _is_json_media_type(media_type):
        if body_truncated:
            return None, "pending"
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "pending"
        return _infer_schema(value), "json"
    if _is_text_media_type(media_type):
        return _new_schema("string"), "text"
    return None, "binary"


def _merge_response(
    responses: dict[str, ResponseIR],
    *,
    status_code: int,
    media_type: str | None,
    observed_schema: SchemaIR | None,
    body_kind: Literal["json", "text", "empty", "binary"],
) -> list[str]:
    exact_key = str(status_code)
    response = responses.get(exact_key)
    changes: list[str] = []
    if response is None:
        baseline = _response_baseline(responses, status_code)
        response = (
            deepcopy(baseline)
            if baseline is not None
            else ResponseIR(
                status_code=exact_key,
                description=f"Observed HTTP {status_code} response",
                headers={},
                contents={},
                links={},
                source_pointer=None,
                raw={},
            )
        )
        response.status_code = exact_key
        responses[exact_key] = response
        changes.append(f"response:{exact_key}")

    if body_kind == "empty":
        if response.contents:
            response.contents = {}
            changes.append(f"response:{exact_key}:no-content")
        return changes

    content_key = media_type or "application/octet-stream"
    media = response.contents.get(content_key)
    if media is None:
        media = MediaTypeIR(
            media_type=content_key,
            schema=None,
            example=None,
            examples={},
            encoding={},
            source_pointer=None,
            raw={},
        )
        response.contents[content_key] = media
        changes.append(f"response:{exact_key}:media:{content_key}")

    if body_kind == "binary":
        return changes
    if observed_schema is None:
        return changes
    if media.schema is None:
        media.schema = observed_schema
        changes.append(f"response:{exact_key}:schema")
        return changes
    if _merge_schema(media.schema, observed_schema):
        changes.append(f"response:{exact_key}:schema")
    return changes


def _response_baseline(
    responses: dict[str, ResponseIR],
    status_code: int,
) -> ResponseIR | None:
    wildcard = f"{status_code // 100}XX"
    for key, response in responses.items():
        if key.upper() == wildcard:
            return response
    return responses.get("default")


def _infer_schema(value: Any) -> SchemaIR:
    if value is None:
        return _new_schema("null")
    if isinstance(value, bool):
        return _new_schema("boolean")
    if isinstance(value, int):
        return _new_schema("integer")
    if isinstance(value, float):
        return _new_schema("number")
    if isinstance(value, str):
        return _new_schema("string")
    if isinstance(value, list):
        item_schema: SchemaIR | None = None
        for item in value:
            inferred = _infer_schema(item)
            if item_schema is None:
                item_schema = inferred
            else:
                _merge_schema(item_schema, inferred)
        schema = _new_schema("array")
        schema.items = item_schema
        return schema
    if isinstance(value, dict):
        schema = _new_schema("object")
        schema.properties = {
            str(name): _infer_schema(child)
            for name, child in value.items()
        }
        return schema
    return _new_schema(None)


def _merge_schema(target: SchemaIR, observed: SchemaIR) -> bool:
    changed = _merge_types(target, observed)
    observed_types = _type_set(observed.type)
    target_types = _type_set(target.type)

    if "object" in observed_types and "object" in target_types:
        for name, child in observed.properties.items():
            current = target.properties.get(name)
            if current is None:
                target.properties[name] = deepcopy(child)
                changed = True
            elif _merge_schema(current, child):
                changed = True

    if "array" in observed_types and "array" in target_types and observed.items is not None:
        if target.items is None:
            target.items = deepcopy(observed.items)
            changed = True
        elif _merge_schema(target.items, observed.items):
            changed = True
    return changed


def _merge_types(target: SchemaIR, observed: SchemaIR) -> bool:
    target_types = _type_set(target.type)
    observed_types = _type_set(observed.type)
    if not observed_types:
        return False
    if not target_types:
        target.type = observed.type
        return True
    if set(observed_types) <= set(target_types):
        return False
    if observed_types == ["integer"] and "number" in target_types:
        return False
    merged = [*target_types]
    for item in observed_types:
        if item not in merged:
            merged.append(item)
    target.type = merged[0] if len(merged) == 1 else merged
    return True


def _type_set(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _new_schema(schema_type: str | None) -> SchemaIR:
    return SchemaIR(
        type=schema_type,
        format=None,
        title=None,
        description=None,
        properties={},
        required=[],
        items=None,
        enum=None,
        const=None,
        default=None,
        nullable=None,
        read_only=None,
        write_only=None,
        deprecated=None,
        minimum=None,
        maximum=None,
        exclusive_minimum=None,
        exclusive_maximum=None,
        min_length=None,
        max_length=None,
        pattern=None,
        min_items=None,
        max_items=None,
        unique_items=None,
        min_properties=None,
        max_properties=None,
        all_of=[],
        any_of=[],
        one_of=[],
        not_schema=None,
        additional_properties=None,
        example=None,
        examples=[],
        discriminator=None,
        xml=None,
        external_docs=None,
        source_pointer=None,
        raw={},
    )


def _is_json_media_type(media_type: str | None) -> bool:
    return media_type == "application/json" or bool(
        media_type and media_type.endswith("+json")
    )


def _is_text_media_type(media_type: str | None) -> bool:
    return bool(media_type and media_type.startswith("text/"))
