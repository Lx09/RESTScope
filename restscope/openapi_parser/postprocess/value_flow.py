"""Value flow indexes builder for OpenAPI specs.

This module builds minimal, agent-facing indexes:
- value_index: normalized producers/consumers
- operation_cards: compact operation summaries
- flow_graph: operation-to-operation flow edges
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ir import OpenAPISpecIR, OperationIR, SchemaIR


def build_value_flow_indexes(ir: "OpenAPISpecIR") -> None:
    """Build minimal agent-facing value-flow indexes and attach to ir.indexes."""
    value_index = _build_value_index(ir)
    ir.indexes.value_index = value_index

    operation_cards = _build_operation_cards(ir)
    ir.indexes.operation_cards = operation_cards

    flow_graph = _build_flow_graph(ir)
    ir.indexes.flow_graph = flow_graph


def _iter_schema_fields(
    schema: "SchemaIR | None",
    *,
    parent_path: str = "",
    required: bool | None = None,
    visited: set[int] | None = None,
):
    """Yield flattened schema fields as (field_path, field_name, schema, required)."""
    if schema is None:
        return

    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        return

    visited = set(visited)
    visited.add(schema_id)

    for child in (schema.all_of or []):
        yield from _iter_schema_fields(child, parent_path=parent_path, required=required, visited=visited)

    for child in (schema.one_of or []) + (schema.any_of or []):
        yield from _iter_schema_fields(child, parent_path=parent_path, required=required, visited=visited)

    if schema.type == "object" and schema.properties:
        required_names = set(schema.required or [])
        for name, child in schema.properties.items():
            field_path = f"{parent_path}.{name}" if parent_path else name
            is_required = name in required_names if required is None else required
            yield field_path, name, child, bool(is_required)
            yield from _iter_schema_fields(child, parent_path=field_path, required=is_required, visited=visited)
        return

    if schema.type == "array" and schema.items is not None:
        array_path = f"{parent_path}[]" if parent_path else "[]"
        yield from _iter_schema_fields(schema.items, parent_path=array_path, required=required, visited=visited)


def _words(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return [p.lower() for p in s.split("_") if p]


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _normalize_resource(resource_name: str | None) -> str | None:
    if not resource_name:
        return None
    parts = _words(resource_name)
    if not parts:
        return None
    return "_".join(_singular(p) for p in parts)


def _normalize_value_name(
    raw_name: str,
    *,
    resource_name: str | None,
    location: str,
) -> tuple[str, str]:
    """Return (normalized_name, kind)."""
    compact = re.sub(r"[^A-Za-z0-9]+", "_", str(raw_name)).strip("_").lower()
    words = _words(raw_name)
    resource = _normalize_resource(resource_name)

    if location == "security":
        return "access.token", "token"

    if compact in {"authorization", "bearer", "bearerauth", "bearer_auth", "token", "access_token", "accesstoken"}:
        return "access.token", "token"

    if compact in {"refresh_token", "refreshtoken"}:
        return "refresh.token", "token"

    if compact in {"status", "state"}:
        if resource:
            return f"{resource}.status", "status"
        return "unknown.status", "status"

    if len(words) >= 2 and words[-1] in {"status", "state"}:
        prefix = "_".join(_singular(w) for w in words[:-1])
        return f"{prefix}.status", "status"

    if compact in {
        "cursor",
        "nextcursor",
        "next_cursor",
        "pagetoken",
        "page_token",
        "nextpagetoken",
        "next_page_token",
    }:
        return "pagination.cursor", "cursor"

    if compact in {"url", "href", "link"} or compact.endswith("url"):
        return ".".join(words), "url"

    if len(words) >= 2 and words[-1] == "id":
        prefix = "_".join(_singular(w) for w in words[:-1])
        return f"{prefix}.id", "id"

    if words == ["id"]:
        if resource:
            return f"{resource}.id", "id"
        return "unknown.id", "id"

    return ".".join(words), "unknown"


def _is_success_status(status_code: str) -> bool:
    return status_code.isdigit() and 200 <= int(status_code) < 300


def _build_value_index(ir: "OpenAPISpecIR"):
    from ..ir import ValueIndexIR

    value_index = ValueIndexIR(
        values={},
        producers={},
        consumers={},
        by_operation={},
    )

    for op_key, op in ir.operations.items():
        resource_name = ir.indexes.operation_resource_map.get(op_key)
        refs = []
        refs.extend(_extract_security_consumers(op_key, op, resource_name))
        refs.extend(_extract_param_consumers(op_key, op, resource_name))
        refs.extend(_extract_body_consumers(op_key, op, resource_name))
        refs.extend(_extract_response_producers(op_key, op, resource_name))

        for ref in refs:
            if ref.kind == "unknown":
                continue
            value_index.values.setdefault(ref.normalized_name, []).append(ref)
            value_index.by_operation.setdefault(op_key, []).append(ref)
            if ref.direction == "produce":
                value_index.producers.setdefault(ref.normalized_name, []).append(ref)
            else:
                value_index.consumers.setdefault(ref.normalized_name, []).append(ref)

    return value_index


def _extract_security_consumers(op_key: str, op: "OperationIR", resource_name: str | None):
    from ..ir import ValueRefIR

    refs = []
    for req in op.security.requirements:
        normalized, kind = _normalize_value_name(
            req.scheme_name,
            resource_name=resource_name,
            location="security",
        )
        refs.append(
            ValueRefIR(
                raw_name=req.scheme_name,
                normalized_name=normalized,
                kind=kind,
                operation_key=op_key,
                direction="consume",
                location="security",
                required=True,
                confidence=0.98,
                reason=f"requires security scheme {req.scheme_name}",
                resource_name=resource_name,
            )
        )
    return refs


def _extract_param_consumers(op_key: str, op: "OperationIR", resource_name: str | None):
    from ..ir import ValueRefIR

    refs = []
    groups = [
        ("path", op.path_parameters, 0.99),
        ("query", op.query_parameters, 0.70),
        ("header", op.header_parameters, 0.75),
        ("cookie", op.cookie_parameters, 0.60),
    ]
    for location, params, confidence in groups:
        for param in params:
            normalized, kind = _normalize_value_name(
                param.name,
                resource_name=resource_name,
                location=location,
            )
            if kind == "unknown":
                continue
            refs.append(
                ValueRefIR(
                    raw_name=param.name,
                    normalized_name=normalized,
                    kind=kind,
                    operation_key=op_key,
                    direction="consume",
                    location=location,
                    required=bool(param.required),
                    confidence=confidence,
                    reason=f"{location} parameter {param.name} consumes {normalized}",
                    resource_name=resource_name,
                    source_pointer=param.source_pointer,
                )
            )
    return refs


def _extract_body_consumers(op_key: str, op: "OperationIR", resource_name: str | None):
    from ..ir import ValueRefIR

    refs = []
    if op.request_body is None:
        return refs

    for media_type, media in op.request_body.contents.items():
        if "json" not in media_type.lower():
            continue
        if media.schema is None:
            continue
        for field_path, field_name, field_schema, is_required in _iter_schema_fields(media.schema):
            normalized, kind = _normalize_value_name(
                field_name,
                resource_name=resource_name,
                location="request_body",
            )
            if kind == "unknown":
                continue
            refs.append(
                ValueRefIR(
                    raw_name=field_name,
                    normalized_name=normalized,
                    kind=kind,
                    operation_key=op_key,
                    direction="consume",
                    location="request_body",
                    required=bool(is_required),
                    confidence=0.85 if is_required else 0.65,
                    reason=f"request body field {field_path} consumes {normalized}",
                    resource_name=resource_name,
                    field_path=field_path,
                    source_pointer=field_schema.source_pointer,
                )
            )
        break

    return refs


def _producer_confidence(kind: str, field_name: str) -> float:
    name = str(field_name).lower()
    if kind == "token":
        return 0.97
    if kind == "id":
        return 0.95 if name != "id" else 0.82
    if kind == "status":
        return 0.75
    if kind == "url":
        return 0.80
    if kind == "cursor":
        return 0.70
    return 0.60


def _extract_response_producers(op_key: str, op: "OperationIR", resource_name: str | None):
    from ..ir import ValueRefIR

    refs = []
    for status_code, response in op.responses.by_status.items():
        if not _is_success_status(str(status_code)):
            continue

        for header_name in response.headers.keys():
            if str(header_name).lower() != "location":
                continue
            normalized = f"{resource_name}.url" if resource_name else "resource.url"
            refs.append(
                ValueRefIR(
                    raw_name=str(header_name),
                    normalized_name=normalized,
                    kind="url",
                    operation_key=op_key,
                    direction="produce",
                    location="response_header",
                    required=False,
                    confidence=0.85,
                    reason="success response Location header produces resource URL",
                    resource_name=resource_name,
                    source_pointer=response.source_pointer,
                )
            )

        for media_type, media in response.contents.items():
            if media.schema is None:
                continue
            for field_path, field_name, field_schema, is_required in _iter_schema_fields(media.schema):
                normalized, kind = _normalize_value_name(
                    field_name,
                    resource_name=resource_name,
                    location="response_body",
                )
                if kind == "unknown":
                    continue
                refs.append(
                    ValueRefIR(
                        raw_name=field_name,
                        normalized_name=normalized,
                        kind=kind,
                        operation_key=op_key,
                        direction="produce",
                        location="response_body",
                        required=bool(is_required),
                        confidence=_producer_confidence(kind, field_name),
                        reason=f"success response field {field_path} produces {normalized}",
                        resource_name=resource_name,
                        field_path=field_path,
                        source_pointer=(
                            field_schema.source_pointer
                            or media.source_pointer
                            or response.source_pointer
                        ),
                    )
                )

    return refs


def _infer_action(op: "OperationIR") -> str:
    method = op.method.upper()
    path = op.path.lower()
    segments = [s for s in path.split("/") if s]
    last = segments[-1] if segments else ""
    has_path_param = any(s.startswith("{") and s.endswith("}") for s in segments)

    if "auth" in segments or last in {"login", "token", "refresh"}:
        return "auth"
    if last in {"upload", "uploads"}:
        return "upload"
    if last in {"download", "downloads"}:
        return "download"
    if last in {"search"}:
        return "search"
    if last in {"status", "state"}:
        return "poll"

    if method == "GET" and has_path_param:
        return "read"
    if method == "GET":
        return "list"
    if method == "POST" and not has_path_param:
        return "create"
    if method == "PUT":
        return "update"
    if method == "PATCH":
        return "patch"
    if method == "DELETE":
        return "delete"
    if method == "POST" and has_path_param:
        return "action"

    return "unknown"


def _request_fields(op: "OperationIR") -> list[str]:
    fields: list[str] = []
    for param in op.path_parameters + op.query_parameters + op.header_parameters + op.cookie_parameters:
        fields.append(param.name)

    if op.request_body is not None:
        for media_type, media in op.request_body.contents.items():
            if "json" not in media_type.lower() or media.schema is None:
                continue
            for field_path, _, _, _ in _iter_schema_fields(media.schema):
                fields.append(field_path)
            break

    return sorted(set(fields))


def _response_fields(op: "OperationIR") -> list[str]:
    fields: list[str] = []
    for status_code, response in op.responses.by_status.items():
        if not _is_success_status(str(status_code)):
            continue
        for media in response.contents.values():
            if media.schema is None:
                continue
            for field_path, _, _, _ in _iter_schema_fields(media.schema):
                fields.append(field_path)
    return sorted(set(fields))


def _risk_signals(
    op: "OperationIR",
    *,
    constraint_tags: list[str],
    likely_consumes: list[str],
    likely_produces: list[str],
) -> list[str]:
    signals: set[str] = set()

    action = _infer_action(op)
    if action != "unknown":
        signals.add(action)

    if "has_security" in constraint_tags:
        signals.add("auth")
    if "has_file_upload" in constraint_tags:
        signals.add("file_upload")
    if "has_enum" in constraint_tags:
        signals.add("enum")
    if "has_pattern" in constraint_tags:
        signals.add("validation")
    if any(value.endswith(".status") for value in likely_consumes + likely_produces):
        signals.add("status")

    for code in op.responses.by_status.keys():
        code = str(code)
        if code in {"401", "403"}:
            signals.add("auth_error")
        if code == "409":
            signals.add("conflict")
        if code.startswith("5"):
            signals.add("server_error")

    return sorted(signals)


def _build_operation_cards(ir: "OpenAPISpecIR"):
    from ..ir import OperationCardIR

    constraint_map: dict[str, list[str]] = {}
    for tag in ir.indexes.constraint_tags:
        constraint_map.setdefault(tag.operation_key, []).append(tag.tag)

    cards: dict[str, OperationCardIR] = {}
    for op_key, op in ir.operations.items():
        resource_name = ir.indexes.operation_resource_map.get(op_key)
        value_refs = ir.indexes.value_index.by_operation.get(op_key, [])

        likely_consumes = sorted({
            ref.normalized_name
            for ref in value_refs
            if ref.direction == "consume"
        })
        likely_produces = sorted({
            ref.normalized_name
            for ref in value_refs
            if ref.direction == "produce"
        })

        constraint_tags = sorted(set(constraint_map.get(op_key, [])))

        cards[op_key] = OperationCardIR(
            operation_key=op_key,
            operation_id=op.operation_id,
            method=op.method,
            path=op.path,
            resource_name=resource_name,
            action=_infer_action(op),
            tags=list(op.tags or []),
            summary=op.summary,
            security=[req.scheme_name for req in op.security.requirements],
            status_codes=sorted(str(code) for code in op.responses.by_status.keys()),
            request_fields=_request_fields(op),
            response_fields=_response_fields(op),
            likely_consumes=likely_consumes,
            likely_produces=likely_produces,
            constraint_tags=constraint_tags,
            risk_signals=_risk_signals(
                op,
                constraint_tags=constraint_tags,
                likely_consumes=likely_consumes,
                likely_produces=likely_produces,
            ),
        )

    return cards


def _flow_confidence(producer, consumer) -> float:
    boost = {
        "path": 1.00,
        "security": 0.95,
        "request_body": 0.85 if consumer.required else 0.65,
        "query": 0.70,
        "header": 0.75,
        "cookie": 0.60,
    }.get(consumer.location, 0.60)
    return round(min(0.99, producer.confidence * consumer.confidence * boost), 3)


def _flow_status(producer, consumer, confidence: float) -> str:
    if consumer.location in {"path", "security"} and confidence >= 0.80:
        return "confirmed"
    if consumer.location == "request_body":
        return "inferred" if confidence >= 0.60 else "uncertain"
    if confidence >= 0.80:
        return "confirmed"
    if confidence >= 0.60:
        return "inferred"
    return "uncertain"


def _possible_flow_edges(ir: "OpenAPISpecIR"):
    from ..ir import FlowEdgeIR

    edges: list[FlowEdgeIR] = []
    value_index = ir.indexes.value_index

    for value_name, producers in value_index.producers.items():
        consumers = value_index.consumers.get(value_name, [])
        for producer in producers:
            for consumer in consumers:
                if producer.operation_key == consumer.operation_key:
                    continue
                confidence = _flow_confidence(producer, consumer)
                edges.append(
                    FlowEdgeIR(
                        source_operation_key=producer.operation_key,
                        target_operation_key=consumer.operation_key,
                        edge_type="possible_flow",
                        value=value_name,
                        confidence=confidence,
                        status=_flow_status(producer, consumer, confidence),
                        reason=(
                            f"{producer.operation_key} produces {value_name}; "
                            f"{consumer.operation_key} consumes {value_name}"
                        ),
                    )
                )
    return edges


def _verify_edges(ir: "OpenAPISpecIR", existing_edges):
    from ..ir import FlowEdgeIR

    edges: list[FlowEdgeIR] = []
    cards = ir.indexes.operation_cards

    for edge in existing_edges:
        if edge.edge_type != "possible_flow":
            continue
        source = cards.get(edge.source_operation_key)
        target = cards.get(edge.target_operation_key)
        if source is None or target is None:
            continue
        if source.resource_name != target.resource_name:
            continue
        if source.action in {"create", "update", "patch", "action"} and target.action in {"read", "list"}:
            edges.append(
                FlowEdgeIR(
                    source_operation_key=edge.source_operation_key,
                    target_operation_key=edge.target_operation_key,
                    edge_type="can_verify",
                    value=edge.value,
                    confidence=min(0.99, edge.confidence + 0.02),
                    status=edge.status,
                    reason=f"{target.operation_key} can verify result of {source.operation_key}",
                )
            )
    return edges


def _cleanup_edges(ir: "OpenAPISpecIR"):
    from ..ir import FlowEdgeIR

    edges: list[FlowEdgeIR] = []
    cards = ir.indexes.operation_cards

    creates = [card for card in cards.values() if card.action == "create" and card.resource_name]
    deletes = [card for card in cards.values() if card.action == "delete" and card.resource_name]

    for create in creates:
        for delete in deletes:
            if create.resource_name != delete.resource_name:
                continue
            shared_values = sorted(set(create.likely_produces) & set(delete.likely_consumes))
            if shared_values:
                edges.append(
                    FlowEdgeIR(
                        source_operation_key=create.operation_key,
                        target_operation_key=delete.operation_key,
                        edge_type="can_cleanup",
                        value=shared_values[0],
                        confidence=0.97,
                        status="confirmed",
                        reason=f"{delete.operation_key} can cleanup resource created by {create.operation_key}",
                    )
                )
            else:
                edges.append(
                    FlowEdgeIR(
                        source_operation_key=create.operation_key,
                        target_operation_key=delete.operation_key,
                        edge_type="can_cleanup",
                        value=None,
                        confidence=0.65,
                        status="inferred",
                        reason=f"{delete.operation_key} may cleanup resource created by {create.operation_key}",
                    )
                )

    return edges


def _auth_setup_edges(ir: "OpenAPISpecIR"):
    from ..ir import FlowEdgeIR

    edges: list[FlowEdgeIR] = []
    cards = ir.indexes.operation_cards

    token_producers = ir.indexes.value_index.producers.get("access.token", [])
    token_consumers = ir.indexes.value_index.consumers.get("access.token", [])

    for producer in token_producers:
        producer_card = cards.get(producer.operation_key)
        if producer_card is None or producer_card.action != "auth":
            continue
        for consumer in token_consumers:
            if producer.operation_key == consumer.operation_key:
                continue
            edges.append(
                FlowEdgeIR(
                    source_operation_key=producer.operation_key,
                    target_operation_key=consumer.operation_key,
                    edge_type="auth_setup",
                    value="access.token",
                    confidence=0.95,
                    status="confirmed",
                    reason=f"{producer.operation_key} produces access token for protected operation {consumer.operation_key}",
                )
            )

    return edges


def _build_flow_graph(ir: "OpenAPISpecIR"):
    from ..ir import FlowGraphIR

    possible = _possible_flow_edges(ir)
    edges = []
    edges.extend(possible)
    edges.extend(_verify_edges(ir, possible))
    edges.extend(_cleanup_edges(ir))
    edges.extend(_auth_setup_edges(ir))
    return FlowGraphIR(edges=edges)

