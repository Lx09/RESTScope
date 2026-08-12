"""Build stable configurable-input identities from operation request IR."""

from __future__ import annotations

import hashlib

from restscope.target_api.media_type import normalize_media_type

from .ir import InputNodeIR, InputNodeKind, OperationIR, ParameterIR, SchemaIR


def build_operation_input_nodes(operation: OperationIR) -> dict[str, InputNodeIR]:
    """Build an order-independent input-node index for one operation."""

    nodes: dict[str, InputNodeIR] = {}
    for parameter in (
        *operation.path_parameters,
        *operation.query_parameters,
        *operation.header_parameters,
        *operation.cookie_parameters,
    ):
        path = _parameter_path(parameter)
        node = _node(
            operation.operation_key,
            kind="parameter",
            path=path,
            parent_id=None,
            schema=parameter.schema,
        )
        nodes[node.input_node_id] = node
        if parameter.schema is not None:
            _add_schema_children(
                nodes,
                operation_key=operation.operation_key,
                schema=parameter.schema,
                parent=node,
                path=path,
            )

    body = operation.request_body
    if body is None:
        return nodes

    body_node = _node(
        operation.operation_key,
        kind="request_body",
        path="body",
        parent_id=None,
        schema=None,
    )
    nodes[body_node.input_node_id] = body_node

    for media_type, media in sorted(body.contents.items()):
        media_path = f"body/{_segment(normalize_media_type(media_type) or '')}"
        media_node = _node(
            operation.operation_key,
            kind="media_type",
            path=media_path,
            parent_id=body_node.input_node_id,
            schema=media.schema,
        )
        nodes[media_node.input_node_id] = media_node
        if media.schema is not None:
            _add_schema_children(
                nodes,
                operation_key=operation.operation_key,
                schema=media.schema,
                parent=media_node,
                path=media_path,
            )
    return nodes


def _add_schema_children(
    nodes: dict[str, InputNodeIR],
    *,
    operation_key: str,
    schema: SchemaIR,
    parent: InputNodeIR,
    path: str,
) -> None:
    """Recursively add property, item, and variant input nodes beneath one Schema location."""
    for property_name, child_schema in sorted(schema.properties.items()):
        if child_schema.read_only:
            continue
        child_path = f"{path}/properties/{_segment(property_name)}"
        child = _schema_node(
            operation_key,
            schema=child_schema,
            path=child_path,
            parent_id=parent.input_node_id,
        )
        nodes[child.input_node_id] = child
        _add_schema_children(
            nodes,
            operation_key=operation_key,
            schema=child_schema,
            parent=child,
            path=child_path,
        )

    if schema.items is not None:
        item_path = f"{path}/items"
        item = _schema_node(
            operation_key,
            schema=schema.items,
            path=item_path,
            parent_id=parent.input_node_id,
        )
        nodes[item.input_node_id] = item
        _add_schema_children(
            nodes,
            operation_key=operation_key,
            schema=schema.items,
            parent=item,
            path=item_path,
        )

    for combiner_name, branches in (
        ("allOf", schema.all_of),
        ("anyOf", schema.any_of),
        ("oneOf", schema.one_of),
    ):
        for index, branch_schema in enumerate(branches):
            branch_path = f"{path}/{combiner_name}/{index}"
            branch = _schema_node(
                operation_key,
                schema=branch_schema,
                path=branch_path,
                parent_id=parent.input_node_id,
            )
            nodes[branch.input_node_id] = branch
            _add_schema_children(
                nodes,
                operation_key=operation_key,
                schema=branch_schema,
                parent=branch,
                path=branch_path,
            )


def _schema_node(
    operation_key: str,
    *,
    schema: SchemaIR,
    path: str,
    parent_id: str,
) -> InputNodeIR:
    return _node(
        operation_key,
        kind=_schema_kind(schema),
        path=path,
        parent_id=parent_id,
        schema=schema,
    )


def _node(
    operation_key: str,
    *,
    kind: InputNodeKind,
    path: str,
    parent_id: str | None,
    schema: SchemaIR | None,
) -> InputNodeIR:
    identity = f"{operation_key}\0{kind}\0{path}".encode()
    return InputNodeIR(
        input_node_id=f"input_{hashlib.sha256(identity).hexdigest()[:32]}",
        node_kind=kind,
        canonical_path=path,
        parent_node_id=parent_id,
        schema=schema,
    )


def _schema_kind(schema: SchemaIR) -> InputNodeKind:
    if schema.all_of or schema.any_of or schema.one_of:
        return "variant"
    if _has_type(schema, "object") or schema.properties:
        return "object"
    if _has_type(schema, "array") or schema.items is not None:
        return "array"
    return "scalar"


def _has_type(schema: SchemaIR, expected: str) -> bool:
    return (
        expected in schema.type
        if isinstance(schema.type, list)
        else schema.type == expected
    )


def _segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _parameter_path(parameter: ParameterIR) -> str:
    name = parameter.name.lower() if parameter.location == "header" else parameter.name
    return f"{_segment(parameter.location)}/{_segment(name)}"
