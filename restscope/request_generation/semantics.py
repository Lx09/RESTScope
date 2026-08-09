"""Stable semantic handles for request inputs exposed to runtime Agents."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from restscope.operation_references import RequestInputReference

from .models import OperationGeneratorConfig


@dataclass(slots=True, frozen=True)
class SemanticInputMap:
    """Map each semantic request handle to its unique frozen input node and back. The immutable map keeps Patch, Constraint, Tool, and Test Case references consistent."""
    handle_by_node: Mapping[str, str]
    node_by_handle: Mapping[str, str]
    reference_by_handle: Mapping[str, RequestInputReference]


def build_semantic_input_map(
    config: OperationGeneratorConfig,
) -> SemanticInputMap:
    """Map active configurable nodes to request-shaped handles."""

    configured_ids = {item.input_node_id for item in config.configs}
    active_root_id = (
        config.snapshot.media_type_node_ids.get(config.active_media_type)
        if config.active_media_type is not None
        else None
    )
    active_root = next(
        (
            node
            for node in config.snapshot.input_nodes
            if node.input_node_id == active_root_id
        ),
        None,
    )
    handle_by_node: dict[str, str] = {}
    node_by_handle: dict[str, str] = {}
    reference_by_handle: dict[str, RequestInputReference] = {}
    for node in config.snapshot.input_nodes:
        if (
            node.input_node_id not in configured_ids
            or node.input_node_id == config.snapshot.request_body_node_id
        ):
            continue
        if node.canonical_path.startswith("body/"):
            if active_root is None or not (
                node.input_node_id == active_root.input_node_id
                or node.canonical_path.startswith(
                    f"{active_root.canonical_path}/"
                )
            ):
                continue
            relative = node.canonical_path.removeprefix(
                active_root.canonical_path
            ).removeprefix("/")
            reference = _body_reference(relative)
        else:
            reference = _parameter_reference(node.canonical_path)
        handle = reference.handle
        if handle in node_by_handle:
            raise ValueError(f"Semantic input handle is not unique: {handle}")
        handle_by_node[node.input_node_id] = handle
        node_by_handle[handle] = node.input_node_id
        reference_by_handle[handle] = reference
    return SemanticInputMap(
        handle_by_node=MappingProxyType(handle_by_node),
        node_by_handle=MappingProxyType(node_by_handle),
        reference_by_handle=MappingProxyType(reference_by_handle),
    )


def _parameter_reference(canonical_path: str) -> RequestInputReference:
    """Adapt one frozen Parameter path to the shared reference Interface."""
    segments = [_unsegment(item) for item in canonical_path.split("/")]
    if len(segments) < 2 or segments[0] not in {
        "path",
        "query",
        "header",
        "cookie",
    }:
        raise ValueError(f"Invalid Parameter canonical path: {canonical_path}")
    reference = RequestInputReference.parameter(segments[0], segments[1])
    return _append_schema_segments(reference, segments[2:])


def _body_reference(relative_path: str) -> RequestInputReference:
    """Adapt one active request-Body path to the shared reference Interface."""
    reference = RequestInputReference.body()
    if not relative_path:
        return reference
    segments = [_unsegment(item) for item in relative_path.split("/")]
    return _append_schema_segments(reference, segments)


def _append_schema_segments(
    reference: RequestInputReference,
    segments: list[str],
) -> RequestInputReference:
    """Translate snapshot Schema path steps through the shared Interface."""
    output = reference
    index = 0
    while index < len(segments):
        segment = segments[index]
        if segment == "properties" and index + 1 < len(segments):
            output = output.property(segments[index + 1])
            index += 2
            continue
        if segment == "items":
            output = output.items()
            index += 1
            continue
        if (
            segment in {"oneOf", "anyOf", "allOf"}
            and index + 1 < len(segments)
        ):
            output = output.variant(segment, int(segments[index + 1]))
            index += 2
            continue
        output = output.property(segment)
        index += 1
    return output


def _unsegment(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")
