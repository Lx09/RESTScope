"""Resource index builder for OpenAPI specs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ir import OpenAPISpecIR, ResourceIndexIR


_COMMON_PREFIX_SEGMENTS = {"api", "app", "rest", "service", "services", "v1", "v2", "v3"}
_IGNORED_MIDDLE_SEGMENTS = {"data"}


def split_path_segments(path: str) -> list[str]:
    """
    Split a path into segments.

    Args:
        path: The path template (e.g., /users/{id}/posts).

    Returns:
        List of segments (e.g., ["users", "{id}", "posts"]).
    """
    segments = [s for s in path.split("/") if s]
    return segments


def _is_parameter_segment(segment: str) -> bool:
    """Return whether the segment is a path parameter placeholder."""
    return segment.startswith("{") and segment.endswith("}")


def _normalize_segment(segment: str) -> str:
    """Normalize one path segment into a resource-friendly token."""
    normalized = re.sub(r"[^a-z0-9]+", "_", segment.strip().lower()).strip("_")
    if not normalized:
        return ""
    if len(normalized) > 3 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    if (
        len(normalized) > 3
        and normalized.endswith("s")
        and not normalized.endswith(("ss", "us", "is"))
    ):
        return normalized[:-1]
    return normalized


def normalize_resource_name(name: str) -> str:
    """Normalize a free-form resource name into the canonical underscore form."""
    parts = [
        _normalize_segment(part)
        for part in re.split(r"[_\W]+", name.strip().lower())
        if part.strip()
    ]
    parts = [part for part in parts if part]
    return "_".join(parts) if parts else "root"


def _extract_meaningful_segments(segments: list[str]) -> list[str]:
    """Drop parameters and common technical prefixes from one path."""
    normalized = [
        _normalize_segment(segment)
        for segment in segments
        if not _is_parameter_segment(segment)
    ]
    normalized = [segment for segment in normalized if segment]

    while len(normalized) > 1 and normalized[0] in _COMMON_PREFIX_SEGMENTS:
        normalized.pop(0)

    filtered: list[str] = []
    for index, segment in enumerate(normalized):
        if segment in _IGNORED_MIDDLE_SEGMENTS and index < len(normalized) - 1:
            continue
        filtered.append(segment)
    return filtered


def infer_resource_name(segments: list[str]) -> str:
    """Infer a simple, stable resource name from path structure."""
    meaningful_segments = _extract_meaningful_segments(segments)
    if not meaningful_segments:
        return "root"
    if len(meaningful_segments) == 1:
        return meaningful_segments[0]
    return "_".join(meaningful_segments[-2:])


def is_item_path(segments: list[str]) -> bool:
    """
    Check if a path represents an item (single resource) operation.

    Args:
        segments: The path segments.

    Returns:
        True if this is an item operation, False otherwise.
    """
    if not segments:
        return False

    # Check if the last segment is a parameter
    last_segment = segments[-1]
    return last_segment.startswith("{") and last_segment.endswith("}")


def is_subresource_path(segments: list[str]) -> bool:
    """
    Check if a path represents a subresource operation.

    A subresource path has more than 2 non-parameter segments.

    Args:
        segments: The path segments.

    Returns:
        True if this is a subresource operation, False otherwise.
    """
    non_param_segments = [
        s for s in segments if not s.startswith("{") and not s.endswith("}")
    ]
    return len(non_param_segments) > 2


def build_resource_index(ir: "OpenAPISpecIR", use_llm: bool = True) -> None:
    """
    Build resource index for the spec.

    Groups operations by a simple deterministic path-based resource rule:
    - collection and item routes share the same base resource
    - deeper non-parameter paths become parent_child resources
    - common technical prefixes are ignored

    Args:
        ir: The OpenAPISpecIR instance to update.
        use_llm: Deprecated compatibility flag. Parser postprocess is always deterministic.
    """
    # Prototype default: keep parser post-processing deterministic and local.
    del use_llm
    _build_path_index(ir)


def get_resource_plan(ir: "OpenAPISpecIR") -> dict[str, list[str]]:
    """Return a plain resource->operations mapping from the current indexes."""
    resources = getattr(getattr(ir, "indexes", None), "resources", {}) or {}
    return {
        str(resource_name): sorted(list(resource_index.operations))
        for resource_name, resource_index in resources.items()
    }


def apply_resource_plan(ir: "OpenAPISpecIR", resource_plan: dict[str, list[str]]) -> None:
    """Overwrite parser indexes from a normalized resource plan."""
    from ..ir import ResourceIndexIR

    resources: dict[str, ResourceIndexIR] = {}
    resource_map: dict[str, str] = {}

    for raw_resource_name, operations in sorted(resource_plan.items()):
        resource_name = normalize_resource_name(raw_resource_name)
        normalized_ops = sorted({str(operation_key) for operation_key in operations if operation_key})
        resources[resource_name] = ResourceIndexIR(
            resource_name=resource_name,
            operations=normalized_ops,
        )
        for operation_key in normalized_ops:
            resource_map[operation_key] = resource_name

    ir.indexes.resources = resources
    ir.indexes.operation_resource_map = resource_map


def _build_path_index(ir: "OpenAPISpecIR") -> None:
    """
    Build resource index using path-based inference.

    This is the parser's canonical deterministic resource mapping.
    """
    from ..ir import ResourceIndexIR

    resources: dict[str, ResourceIndexIR] = {}
    resource_map: dict[str, str] = {}

    for op_key, op in ir.operations.items():
        segments = split_path_segments(op.path)
        resource_name = infer_resource_name(segments)
        resource_map[op_key] = resource_name

        if resource_name not in resources:
            resources[resource_name] = ResourceIndexIR(
                resource_name=resource_name,
                operations=[]
            )

        resources[resource_name].operations.append(op_key)

    for resource_index in resources.values():
        resource_index.operations = sorted(resource_index.operations)

    ir.indexes.resources = dict(sorted(resources.items()))
    ir.indexes.operation_resource_map = resource_map
