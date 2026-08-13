"""Decode one response once and validate it against an OpenAPI response Contract.

The Transport Pipeline creates :class:`ResponseEvidence` immediately after the
Observation stage. The current Contract Monitor and immutable-baseline Bug
Oracle then share this value, so JSON decoding and media normalization cannot
quietly disagree between the two decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for

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


@dataclass(frozen=True, slots=True)
class ContractMismatch:
    """Describe one bounded location where response evidence violates a Contract."""

    code: str
    contract_pointer: str
    instance_pointer: str
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class ContractValidationResult:
    """Return all bounded mismatches found by one pure validation pass."""

    mismatches: tuple[ContractMismatch, ...] = ()

    @property
    def matched(self) -> bool:
        """Return whether no Contract mismatch was found."""

        return not self.mismatches


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
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        body_kind: Literal["text", "binary"] = "binary"
    else:
        body_kind = "text"
    return ResponseEvidence(
        status_code=status_code,
        media_type=media_type,
        headers=normalized_headers,
        body=body,
        body_kind=body_kind,
    )


class ContractValidator:
    """Validate response evidence against one normalized OpenAPI document."""

    def validate(
        self,
        *,
        document: dict[str, object],
        operation_path: str,
        operation_method: str,
        evidence: ResponseEvidence,
    ) -> ContractValidationResult:
        """Check status, media, body Schema, and actual declared header values."""

        response, response_pointer = _select_response(
            document,
            operation_path=operation_path,
            operation_method=operation_method,
            status_code=evidence.status_code,
        )
        if response is None:
            return ContractValidationResult(
                (_mismatch(
                    "undeclared_status",
                    f"{_operation_pointer(operation_path, operation_method)}/responses",
                    "/status_code",
                    "an exact, wildcard, or default response",
                    str(evidence.status_code),
                ),)
            )

        mismatches: list[ContractMismatch] = []
        content = response.get("content")
        content_map = content if isinstance(content, dict) else {}
        if evidence.body:
            media_schema, media_pointer = _select_media_schema(
                content_map,
                evidence.media_type,
                response_pointer=response_pointer,
            )
            if media_schema is None:
                mismatches.append(
                    _mismatch(
                        "undeclared_media_type",
                        f"{response_pointer}/content",
                        "/headers/content-type",
                        "a declared response media type",
                        evidence.media_type or "missing",
                    )
                )
            elif evidence.body_kind == "invalid_json":
                mismatches.append(
                    _mismatch(
                        "invalid_json_body",
                        media_pointer,
                        "/body",
                        "valid UTF-8 JSON",
                        "invalid JSON bytes",
                    )
                )
            elif evidence.body_kind == "json":
                schema = media_schema.get("schema")
                if isinstance(schema, dict):
                    mismatches.extend(
                        _validate_json_schema(
                            schema,
                            evidence.json_value,
                            contract_pointer=f"{media_pointer}/schema",
                        )
                    )
        declared_headers = response.get("headers")
        if isinstance(declared_headers, dict):
            for declared_name, header in declared_headers.items():
                actual = evidence.headers.get(str(declared_name).lower())
                if actual is None or not isinstance(header, dict):
                    continue
                schema = header.get("schema")
                if isinstance(schema, dict):
                    mismatches.extend(
                        _validate_json_schema(
                            schema,
                            actual,
                            contract_pointer=(
                                f"{response_pointer}/headers/"
                                f"{_escape_pointer(str(declared_name))}/schema"
                            ),
                            instance_pointer=(
                                f"/headers/{_escape_pointer(str(declared_name).lower())}"
                            ),
                        )
                    )
        return ContractValidationResult(tuple(mismatches[:20]))


def _select_response(
    document: dict[str, object],
    *,
    operation_path: str,
    operation_method: str,
    status_code: int,
) -> tuple[dict[str, object] | None, str]:
    """Select exact, class wildcard, then default response in OpenAPI order."""

    paths = document.get("paths")
    path_item = paths.get(operation_path) if isinstance(paths, dict) else None
    operation = (
        path_item.get(operation_method.lower()) if isinstance(path_item, dict) else None
    )
    responses = operation.get("responses") if isinstance(operation, dict) else None
    response_map = responses if isinstance(responses, dict) else {}
    keys = (str(status_code), f"{status_code // 100}XX", f"{status_code // 100}xx", "default")
    root = f"{_operation_pointer(operation_path, operation_method)}/responses"
    for key in keys:
        response = response_map.get(key)
        if isinstance(response, dict):
            return response, f"{root}/{_escape_pointer(key)}"
    return None, root


def _select_media_schema(
    content: dict[object, object],
    media_type: str | None,
    *,
    response_pointer: str,
) -> tuple[dict[str, object] | None, str]:
    """Select an exact or wildcard OpenAPI media entry."""

    if media_type is None:
        return None, f"{response_pointer}/content"
    major = media_type.split("/", 1)[0]
    for key in (media_type, f"{major}/*", "*/*"):
        value = content.get(key)
        if isinstance(value, dict):
            return value, f"{response_pointer}/content/{_escape_pointer(key)}"
    return None, f"{response_pointer}/content"


def _validate_json_schema(
    schema: dict[str, object],
    instance: object,
    *,
    contract_pointer: str,
    instance_pointer: str = "/body",
) -> list[ContractMismatch]:
    """Translate jsonschema diagnostics into bounded stable mismatch evidence."""

    try:
        validator_class = validator_for(schema)
        validator_class.check_schema(schema)
        errors = sorted(
            validator_class(schema).iter_errors(instance),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    except SchemaError as exc:
        return [_mismatch(
            "invalid_contract_schema",
            contract_pointer,
            instance_pointer,
            "a valid JSON Schema",
            _bounded(str(exc), 200),
        )]
    return [
        _schema_mismatch(
            error,
            contract_pointer=contract_pointer,
            instance_pointer=instance_pointer,
        )
        for error in errors[:20]
    ]


def _schema_mismatch(
    error: ValidationError,
    *,
    contract_pointer: str,
    instance_pointer: str,
) -> ContractMismatch:
    """Create one compact mismatch without retaining the full response value."""

    schema_path = "/".join(_escape_pointer(str(item)) for item in error.absolute_schema_path)
    value_path = "/".join(_escape_pointer(str(item)) for item in error.absolute_path)
    return _mismatch(
        "schema_validation_failed",
        f"{contract_pointer}/{schema_path}" if schema_path else contract_pointer,
        f"{instance_pointer}/{value_path}" if value_path else instance_pointer,
        _bounded(error.message, 300),
        _bounded(repr(error.instance), 200),
    )


def _operation_pointer(path: str, method: str) -> str:
    return f"/paths/{_escape_pointer(path)}/{method.lower()}"


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _mismatch(
    code: str,
    contract_pointer: str,
    instance_pointer: str,
    expected: str,
    actual: str,
) -> ContractMismatch:
    return ContractMismatch(code, contract_pointer, instance_pointer, expected, actual)
