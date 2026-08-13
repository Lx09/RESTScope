"""Deterministic matching between concrete request paths and OpenAPI operations."""

from __future__ import annotations

import re

from .ir import OpenAPISpecIR, OperationIR

_TEMPLATE_SEGMENT = re.compile(r"^\{[^{}]+\}$")


class OpenAPIOperationMatchError(LookupError):
    """A concrete method/path cannot identify exactly one OpenAPI operation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        operation_keys: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.operation_keys = operation_keys


def match_operation(
    ir: OpenAPISpecIR,
    *,
    method: str,
    path: str,
) -> OperationIR:
    """Return the unique operation matching a template or concrete path."""

    normalized_method = method.strip().upper()
    exact = [
        operation
        for operation in ir.operations.values()
        if operation.method.upper() == normalized_method and operation.path == path
    ]
    matches = exact or [
        operation
        for operation in ir.operations.values()
        if operation.method.upper() == normalized_method
        and _path_matches(operation.path, path)
    ]
    if not matches:
        raise OpenAPIOperationMatchError(
            "operation_match_not_found",
            f"OpenAPI operation not found: {normalized_method} {path}",
        )
    if len(matches) > 1:
        keys = tuple(sorted(operation.operation_key for operation in matches))
        raise OpenAPIOperationMatchError(
            "operation_match_ambiguous",
            f"Request path matches {len(keys)} OpenAPI operations",
            operation_keys=keys,
        )
    return matches[0]


def _path_matches(template: str, concrete: str) -> bool:
    template_segments = template.strip("/").split("/") if template != "/" else []
    concrete_segments = concrete.strip("/").split("/") if concrete != "/" else []
    if len(template_segments) != len(concrete_segments):
        return False
    return all(
        bool(concrete_segment)
        and (
            _TEMPLATE_SEGMENT.fullmatch(template_segment) is not None
            or template_segment == concrete_segment
        )
        for template_segment, concrete_segment in zip(
            template_segments,
            concrete_segments,
            strict=True,
        )
    )
