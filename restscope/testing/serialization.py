"""Serialize structured TestCases according to OpenAPI parameter rules."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from .models import (
    GeneratedTestCase,
    OperationTestSnapshot,
    ParameterSnapshot,
    PreparedTestRequest,
)


class SerializationError(ValueError):
    """A TestCase cannot be represented by the operation's request contract."""

    code = "serialization_failed"


def serialize_test_case(
    operation: OperationTestSnapshot,
    case: GeneratedTestCase,
) -> PreparedTestRequest:
    """Serialize one generated TestCase without resolving a target base URL."""

    if operation.operation_key != case.operation_key:
        raise SerializationError("TestCase belongs to a different operation")

    path = operation.path
    for parameter in _parameters_at(operation, "path"):
        if parameter.name not in case.path_parameters:
            raise SerializationError(f"Missing path parameter: {parameter.name}")
        rendered = serialize_path_parameter_value(
            parameter,
            case.path_parameters[parameter.name],
        )
        path = path.replace(f"{{{parameter.name}}}", rendered)
    if "{" in path or "}" in path:
        raise SerializationError(f"Unresolved path template: {path}")

    query_items: list[tuple[str, str]] = []
    query_allow_reserved_indices: list[int] = []
    for parameter in _parameters_at(operation, "query"):
        if parameter.name in case.query_parameters:
            serialized = _serialize_query(
                parameter,
                case.query_parameters[parameter.name],
            )
            start = len(query_items)
            query_items.extend(serialized)
            if parameter.allow_reserved:
                query_allow_reserved_indices.extend(
                    range(start, start + len(serialized))
                )

    headers: dict[str, str] = {}
    for parameter in _parameters_at(operation, "header"):
        if parameter.name in case.header_parameters:
            headers[parameter.name] = _serialize_header(
                parameter,
                case.header_parameters[parameter.name],
            )

    cookies: list[str] = []
    for parameter in _parameters_at(operation, "cookie"):
        if parameter.name in case.cookie_parameters:
            cookies.extend(_serialize_cookie(parameter, case.cookie_parameters[parameter.name]))
    if cookies:
        headers["Cookie"] = "; ".join(cookies)

    content: bytes | None = None
    if case.body_present or case.body is not None:
        if not case.media_type:
            raise SerializationError("Request body has no media type")
        headers["Content-Type"] = case.media_type
        content = _serialize_body(case.media_type, case.body)

    return PreparedTestRequest(
        method=operation.method.upper(),
        path=path,
        query_items=query_items,
        query_allow_reserved_indices=query_allow_reserved_indices,
        headers=headers,
        content=content,
    )


def serialize_path_parameter_value(parameter: ParameterSnapshot, value: Any) -> str:
    """Serialize one path value; also used to build safe request evidence."""

    style = parameter.style or "simple"
    explode = parameter.explode if parameter.explode is not None else False
    name = _encode(parameter.name, allow_reserved=bool(parameter.allow_reserved))
    if style == "simple":
        return _simple_value(value, explode=explode, allow_reserved=bool(parameter.allow_reserved))
    if style == "label":
        delimiter = "." if explode and isinstance(value, list) else ","
        return "." + _delimited_value(
            value,
            delimiter=delimiter,
            explode=explode,
            allow_reserved=bool(parameter.allow_reserved),
        )
    if style == "matrix":
        if isinstance(value, dict) and explode:
            return "".join(
                f";{_encode(str(key))}={_encode_value(item)}"
                for key, item in sorted(value.items())
            )
        if isinstance(value, list) and explode:
            return "".join(f";{name}={_encode_value(item)}" for item in value)
        return f";{name}={_delimited_value(value, delimiter=',', explode=False)}"
    raise SerializationError(f"Unsupported path parameter style: {style}")


def _serialize_query(parameter: ParameterSnapshot, value: Any) -> list[tuple[str, str]]:
    """
    Serialize query for deterministic request generation, constraint solving, and
    execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    collection_format = parameter.collection_format
    if isinstance(value, list) and isinstance(collection_format, str):
        return _legacy_collection(parameter.name, value, collection_format)
    if isinstance(value, list) and parameter.swagger:
        return _legacy_collection(parameter.name, value, "csv")

    style = parameter.style or "form"
    explode = parameter.explode if parameter.explode is not None else style == "form"
    name = parameter.name
    if style == "deepObject":
        if not isinstance(value, dict) or not explode:
            raise SerializationError("deepObject query parameters require an exploded object")
        return [(f"{name}[{key}]", _text(item)) for key, item in sorted(value.items())]
    if style == "form":
        if isinstance(value, list):
            return [(name, _text(item)) for item in value] if explode else [(name, ",".join(_text(item) for item in value))]
        if isinstance(value, dict):
            if explode:
                return [(str(key), _text(item)) for key, item in sorted(value.items())]
            return [(name, _flatten_object(value, delimiter=","))]
        return [(name, _text(value))]
    if style in {"spaceDelimited", "pipeDelimited"}:
        delimiter = " " if style == "spaceDelimited" else "|"
        if isinstance(value, list):
            return [(name, delimiter.join(_text(item) for item in value))]
        if isinstance(value, dict):
            return [(name, _flatten_object(value, delimiter=delimiter))]
        return [(name, _text(value))]
    raise SerializationError(f"Unsupported query parameter style: {style}")


def _serialize_header(parameter: ParameterSnapshot, value: Any) -> str:
    style = parameter.style or "simple"
    if style != "simple":
        raise SerializationError(f"Unsupported header parameter style: {style}")
    explode = parameter.explode if parameter.explode is not None else False
    return _delimited_value(value, delimiter=",", explode=explode)


def _serialize_cookie(parameter: ParameterSnapshot, value: Any) -> list[str]:
    style = parameter.style or "form"
    explode = parameter.explode if parameter.explode is not None else True
    if style != "form":
        raise SerializationError(f"Unsupported cookie parameter style: {style}")
    if isinstance(value, list):
        if explode:
            return [f"{parameter.name}={_text(item)}" for item in value]
        return [f"{parameter.name}={','.join(_text(item) for item in value)}"]
    if isinstance(value, dict):
        if explode:
            return [f"{key}={_text(item)}" for key, item in sorted(value.items())]
        return [f"{parameter.name}={_flatten_object(value, delimiter=',')}"]
    return [f"{parameter.name}={_text(value)}"]


def _serialize_body(media_type: str, body: Any) -> bytes:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized == "application/json" or normalized.endswith("+json"):
        try:
            return json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise SerializationError("Generated JSON request body is not serializable") from exc
    if normalized == "application/x-www-form-urlencoded":
        if not isinstance(body, dict):
            raise SerializationError("Form request body must be an object")
        return urlencode(_form_items(body)).encode()
    if normalized.startswith("text/"):
        if not isinstance(body, str):
            raise SerializationError("Text request body must be a string")
        return body.encode()
    raise SerializationError(f"Unsupported request media type: {media_type}")


def _legacy_collection(name: str, value: list[Any], collection_format: str) -> list[tuple[str, str]]:
    if collection_format == "multi":
        return [(name, _text(item)) for item in value]
    delimiter = {"csv": ",", "ssv": " ", "tsv": "\t", "pipes": "|"}.get(collection_format)
    if delimiter is None:
        raise SerializationError(f"Unsupported Swagger collectionFormat: {collection_format}")
    return [(name, delimiter.join(_text(item) for item in value))]


def _parameters_at(
    operation: OperationTestSnapshot,
    location: str,
) -> list[ParameterSnapshot]:
    return [
        parameter
        for parameter in operation.parameters
        if parameter.location == location
    ]


def _simple_value(value: Any, *, explode: bool, allow_reserved: bool) -> str:
    return _delimited_value(
        value,
        delimiter=",",
        explode=explode,
        allow_reserved=allow_reserved,
    )


def _delimited_value(
    value: Any,
    *,
    delimiter: str,
    explode: bool,
    allow_reserved: bool = False,
) -> str:
    if isinstance(value, list):
        return delimiter.join(_encode_value(item, allow_reserved=allow_reserved) for item in value)
    if isinstance(value, dict):
        if explode:
            return delimiter.join(
                f"{_encode(str(key))}={_encode_value(item, allow_reserved=allow_reserved)}"
                for key, item in sorted(value.items())
            )
        values: list[str] = []
        for key, item in sorted(value.items()):
            values.extend((_encode(str(key)), _encode_value(item, allow_reserved=allow_reserved)))
        return delimiter.join(values)
    return _encode_value(value, allow_reserved=allow_reserved)


def _flatten_object(value: dict[Any, Any], *, delimiter: str) -> str:
    flattened: list[str] = []
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        flattened.extend((str(key), _text(item)))
    return delimiter.join(flattened)


def _form_items(value: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, item in value.items():
        values = item if isinstance(item, list) else [item]
        items.extend((key, _text(entry)) for entry in values)
    return items


def _encode_value(value: Any, *, allow_reserved: bool = False) -> str:
    return _encode(_text(value), allow_reserved=allow_reserved)


def _encode(value: str, *, allow_reserved: bool = False) -> str:
    safe = ":/?#[]@!$&'()*+,;=" if allow_reserved else ""
    return quote(value, safe=safe)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)
