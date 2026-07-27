"""Freeze OpenAPI request contracts and infer their first generator set."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from pydantic import ValidationError

from restscope.openapi_parser import OpenAPISpecIR
from restscope.openapi_parser.ir import OperationIR, ParameterIR, SchemaIR

from .models import (
    GeneratorDisabledReason,
    InputGeneratorConfig,
    InputNodeSnapshot,
    OperationGeneratorConfig,
    OperationTestSnapshot,
    ParameterSnapshot,
    SchemaSnapshot,
)


def build_initial_operation_config(operation: OperationIR) -> OperationGeneratorConfig:
    """Freeze one operation and infer a conservative Generator for every input.

    Unsupported selected inputs disable the operation with explicit reasons.
    Fallback strategies still keep the record structurally inspectable without
    pretending the operation is safe to execute.
    """

    snapshot, reasons = build_operation_snapshot(operation)
    # A concrete configuration selects one request media type. Preference
    # ordering chooses JSON-compatible contracts before form encodings.
    active_media_type = snapshot.available_media_types[0] if snapshot.available_media_types else None
    selected_node_ids = _selected_node_ids(snapshot, active_media_type)
    configs: list[InputGeneratorConfig] = []
    for node in snapshot.input_nodes:
        strategy, reason = _default_strategy(node)
        if reason is not None and node.input_node_id in selected_node_ids:
            reasons.append(reason)
        try:
            # Required nodes are always present. Optional nodes start at 50% so
            # a batch can exercise both inclusion and omission.
            config = InputGeneratorConfig(
                input_node_id=node.input_node_id,
                inclusion_probability=1.0 if node.required else 0.5,
                strategy=strategy,
            )
        except ValidationError:
            if node.input_node_id in selected_node_ids:
                reasons.append(
                    _unavailable_reason(
                        node,
                        "derived strategy options are internally inconsistent",
                    )
                )
            config = InputGeneratorConfig(
                input_node_id=node.input_node_id,
                inclusion_probability=1.0 if node.required else 0.5,
                strategy=_fallback_strategy(node),
            )
        configs.append(config)
    if snapshot.request_body_node_id is not None and active_media_type is None:
        reasons.append(
            GeneratorDisabledReason(
                code="request_body_media_type_unsupported",
                message="Operation has no supported request body media type",
            )
        )
    return OperationGeneratorConfig(
        operation_key=operation.operation_key,
        revision=1,
        snapshot=snapshot,
        enabled=not reasons,
        disabled_reasons=_deduplicate_reasons(reasons),
        active_media_type=active_media_type,
        configs=configs,
    )


def build_initial_catalog(ir: OpenAPISpecIR) -> list[OperationGeneratorConfig]:
    """Build revision-one Generator configurations for every parsed operation."""
    return [
        build_initial_operation_config(operation)
        for operation in ir.operations.values()
    ]


def build_operation_snapshot(
    operation: OperationIR,
) -> tuple[OperationTestSnapshot, list[GeneratorDisabledReason]]:
    """Project mutable OpenAPI IR into the immutable testing contract.

    The snapshot retains parameter serialization rules, supported media types,
    schema constraints, defaults/examples, and semantic input nodes. Parser-only
    objects and unsupported media branches are omitted so later generation has
    one explicit interpretation.
    """
    reasons: list[GeneratorDisabledReason] = []
    node_by_path = {
        node.canonical_path: node for node in operation.input_nodes.values()
    }
    parameters: list[ParameterSnapshot] = []
    unsupported_parameter_nodes: dict[str, str] = {}
    for parameter in _parameters(operation):
        path = _parameter_path(parameter)
        node = node_by_path[path]
        reason = _parameter_unsupported_reason(parameter)
        if reason is not None:
            reasons.append(reason)
            unsupported_parameter_nodes[node.input_node_id] = reason.message
        parameters.append(
            ParameterSnapshot(
                input_node_id=node.input_node_id,
                name=parameter.name,
                location=parameter.location,
                required=parameter.required,
                style=parameter.style,
                explode=parameter.explode,
                allow_reserved=parameter.allow_reserved,
                collection_format=parameter.raw.get("collectionFormat"),
                swagger="schema" not in parameter.raw and "type" in parameter.raw,
            )
        )

    body_node = node_by_path.get("body")
    media_type_node_ids: dict[str, str] = {}
    media_type_encodings: dict[str, dict[str, Any]] = {}
    supported_media_types: list[str] = []
    if operation.request_body is not None:
        for media_type, media in operation.request_body.contents.items():
            normalized = media_type.strip().lower()
            node = node_by_path[f"body/{_segment(normalized)}"]
            media_type_encodings[normalized] = dict(media.encoding)
            if _is_supported_media_contract(
                normalized,
                schema=media.schema,
                encoding=media.encoding,
            ):
                media_type_node_ids[normalized] = node.input_node_id
                supported_media_types.append(normalized)
    supported_media_types.sort(key=_media_type_priority)
    supported_body_paths = {
        f"body/{_segment(media_type)}"
        for media_type in supported_media_types
    }

    snapshots = [
        InputNodeSnapshot(
            input_node_id=node.input_node_id,
            node_kind=node.node_kind,
            canonical_path=node.canonical_path,
            parent_node_id=node.parent_node_id,
            required=_node_is_required(operation, node.canonical_path, node.parent_node_id),
            schema_contract=_schema_snapshot(node.schema),
        )
        for node in operation.input_nodes.values()
        if (
            not node.canonical_path.startswith("body/")
            or any(
                node.canonical_path == path
                or node.canonical_path.startswith(f"{path}/")
                for path in supported_body_paths
            )
        )
    ]
    unsupported_schema_nodes: dict[str, list[str]] = {}
    recoverable_schema_reasons: dict[str, list[GeneratorDisabledReason]] = {}
    for node in snapshots:
        if node.schema_contract is not None:
            for reason in _schema_unsupported_reasons(node):
                if reason.recoverable:
                    recoverable_schema_reasons.setdefault(
                        node.input_node_id,
                        [],
                    ).append(reason)
                else:
                    unsupported_schema_nodes.setdefault(
                        node.input_node_id,
                        [],
                    ).append(reason.message)

    snapshot = OperationTestSnapshot(
        operation_key=operation.operation_key,
        method=operation.method.upper(),
        path=operation.path,
        parameters=parameters,
        request_body_node_id=body_node.input_node_id if body_node is not None else None,
        media_type_node_ids=media_type_node_ids,
        media_type_encodings=media_type_encodings,
        available_media_types=supported_media_types,
        unsupported_parameter_nodes=unsupported_parameter_nodes,
        unsupported_schema_nodes=unsupported_schema_nodes,
        input_nodes=snapshots,
    )
    active_media_type = supported_media_types[0] if supported_media_types else None
    selected_node_ids = _selected_node_ids(snapshot, active_media_type)
    for node_id, messages in unsupported_schema_nodes.items():
        if node_id in selected_node_ids:
            reasons.extend(
                GeneratorDisabledReason(
                    code="request_schema_unsupported",
                    message=message,
                )
                for message in messages
            )
    for node_id, node_reasons in recoverable_schema_reasons.items():
        if node_id in selected_node_ids:
            reasons.extend(node_reasons)
    return snapshot, _deduplicate_reasons(reasons)


def _schema_snapshot(schema: SchemaIR | None) -> SchemaSnapshot | None:
    """
    Handle schema snapshot as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if schema is None:
        return None
    additional = schema.additional_properties
    read_only_properties = sorted(_read_only_property_names(schema))
    return SchemaSnapshot(
        type=schema.type,
        format=schema.format,
        properties={
            name: _schema_snapshot(child)
            for name, child in schema.properties.items()
            if not child.read_only
        },
        read_only_properties=read_only_properties,
        required=[
            name
            for name in schema.required
            if name not in read_only_properties
        ],
        items=_schema_snapshot(schema.items),
        enum=(
            [_project_request_value(schema, value) for value in schema.enum]
            if schema.enum is not None
            else None
        ),
        const=_project_request_value(schema, schema.const),
        has_const="const" in schema.raw,
        default=_project_request_value(schema, schema.default),
        has_default="default" in schema.raw,
        example=_project_request_value(schema, schema.example),
        has_example="example" in schema.raw,
        nullable=schema.nullable,
        minimum=schema.minimum,
        maximum=schema.maximum,
        exclusive_minimum=schema.exclusive_minimum,
        exclusive_maximum=schema.exclusive_maximum,
        multiple_of=schema.raw.get("multipleOf"),
        min_length=schema.min_length,
        max_length=schema.max_length,
        pattern=schema.pattern,
        min_items=schema.min_items,
        max_items=schema.max_items,
        unique_items=schema.unique_items,
        min_properties=schema.min_properties,
        max_properties=schema.max_properties,
        additional_properties=(
            _schema_snapshot(additional)
            if isinstance(additional, SchemaIR)
            else additional
        ),
        all_of=[_schema_snapshot(item) for item in schema.all_of],
        any_of=[_schema_snapshot(item) for item in schema.any_of],
        one_of=[_schema_snapshot(item) for item in schema.one_of],
        has_not=schema.not_schema is not None,
        has_conditional=any(key in schema.raw for key in ("if", "then", "else")),
    )


def _project_request_value(schema: SchemaIR, value: Any) -> Any:
    """Remove response-only properties from frozen concrete request values."""

    if isinstance(value, list):
        if schema.items is None:
            return deepcopy(value)
        return [_project_request_value(schema.items, item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)

    read_only_names = _read_only_property_names(schema)
    projected: dict[Any, Any] = {}
    for name, item in value.items():
        if name in read_only_names:
            continue
        property_schema = _request_property_schema(schema, name)
        projected[name] = (
            _project_request_value(property_schema, item)
            if property_schema is not None
            else deepcopy(item)
        )
    return projected


def _read_only_property_names(
    schema: SchemaIR,
    *,
    visited: set[int] | None = None,
) -> set[str]:
    visited = set() if visited is None else visited
    identity = id(schema)
    if identity in visited:
        return set()
    visited.add(identity)
    names = {
        name
        for name, child in schema.properties.items()
        if child.read_only
    }
    for branch in (*schema.all_of, *schema.any_of, *schema.one_of):
        names.update(_read_only_property_names(branch, visited=visited))
    return names


def _request_property_schema(
    schema: SchemaIR,
    name: Any,
    *,
    visited: set[int] | None = None,
) -> SchemaIR | None:
    if not isinstance(name, str):
        return None
    visited = set() if visited is None else visited
    identity = id(schema)
    if identity in visited:
        return None
    visited.add(identity)
    direct = schema.properties.get(name)
    if direct is not None:
        return direct
    for branch in (*schema.all_of, *schema.any_of, *schema.one_of):
        found = _request_property_schema(branch, name, visited=visited)
        if found is not None:
            return found
    return None


def _default_strategy(
    node: InputNodeSnapshot,
) -> tuple[dict[str, Any], GeneratorDisabledReason | None]:
    """
    Handle default strategy as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if node.node_kind == "request_body":
        return {"type": "request_body"}, None
    schema = node.schema_contract
    if schema is None:
        return (
            {"type": "constant", "value": None},
            GeneratorDisabledReason(
                code="default_generator_unavailable",
                message=f"Input has no schema: {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            ),
        )
    if schema.enum is not None:
        if schema.enum:
            return {"type": "choice", "values": schema.enum}, None
        return (
            _fallback_strategy(node),
            _unavailable_reason(node, "enum contains no values"),
        )
    if schema.has_const:
        return _explicit_default(node, schema.const)
    if schema.has_default:
        return _explicit_default(node, schema.default)
    if schema.has_example:
        return _explicit_default(node, schema.example)
    if schema.one_of or schema.any_of:
        branches = schema.one_of or schema.any_of
        return {"type": "variant", "branch_weights": [1.0] * len(branches)}, None
    if schema.all_of or _has_type(schema, "object") or schema.properties:
        return {"type": "object"}, None
    if _has_type(schema, "array") or schema.items is not None:
        minimum = schema.min_items if schema.min_items is not None else 1
        maximum = schema.max_items if schema.max_items is not None else max(1, minimum)
        return {
            "type": "array",
            "min_items": minimum,
            "max_items": maximum,
        }, None
    if schema.format in {"uuid", "date", "date-time", "email"}:
        return {"type": "format", "format": schema.format}, None
    if _has_type(schema, "boolean"):
        return {"type": "boolean"}, None
    if _has_type(schema, "integer"):
        minimum, maximum = _integer_bounds(schema)
        return {"type": "integer_range", "minimum": minimum, "maximum": maximum}, None
    if _has_type(schema, "number"):
        minimum, maximum = _number_bounds(schema)
        return {"type": "number_range", "minimum": minimum, "maximum": maximum}, None
    if _has_type(schema, "string") or schema.type is None:
        minimum = schema.min_length if schema.min_length is not None else 1
        maximum = schema.max_length if schema.max_length is not None else max(16, minimum)
        return {
            "type": "random_string",
            "min_length": minimum,
            "max_length": maximum,
        }, None
    if _has_type(schema, "null"):
        return {"type": "constant", "value": None}, None
    return (
        {"type": "constant", "value": None},
        _unavailable_reason(node, "no supported default strategy"),
    )


def _explicit_default(
    node: InputNodeSnapshot,
    value: Any,
) -> tuple[dict[str, Any], GeneratorDisabledReason | None]:
    from .generation import schema_matches

    assert node.schema_contract is not None
    strategy = {"type": "constant", "value": value}
    if schema_matches(node.schema_contract, value):
        return strategy, None
    return strategy, _unavailable_reason(
        node,
        "const/default/example violates the frozen constraints",
    )


def _unavailable_reason(
    node: InputNodeSnapshot,
    detail: str,
) -> GeneratorDisabledReason:
    return GeneratorDisabledReason(
        code="default_generator_unavailable",
        message=f"Default generator unavailable at {node.canonical_path}: {detail}",
        recoverable=True,
        input_node_id=node.input_node_id,
    )


def _fallback_strategy(node: InputNodeSnapshot) -> dict[str, Any]:
    if node.node_kind == "request_body":
        return {"type": "request_body"}
    schema = node.schema_contract
    if schema is None:
        return {"type": "constant", "value": None}
    if schema.one_of or schema.any_of:
        return {
            "type": "variant",
            "branch_weights": [1.0] * len(schema.one_of or schema.any_of),
        }
    if schema.all_of or _has_type(schema, "object") or schema.properties:
        return {"type": "object"}
    if _has_type(schema, "array") or schema.items is not None:
        return {"type": "array", "min_items": 0, "max_items": 0}
    return {"type": "constant", "value": None}


def _integer_bounds(schema: SchemaSnapshot) -> tuple[int, int]:
    minimum = math.ceil(schema.minimum) if schema.minimum is not None else 0
    maximum = math.floor(schema.maximum) if schema.maximum is not None else minimum + 100
    if isinstance(schema.exclusive_minimum, bool):
        if schema.exclusive_minimum and schema.minimum is not None:
            minimum = math.floor(schema.minimum) + 1
    elif schema.exclusive_minimum is not None:
        minimum = math.floor(schema.exclusive_minimum) + 1
    if isinstance(schema.exclusive_maximum, bool):
        if schema.exclusive_maximum and schema.maximum is not None:
            maximum = math.ceil(schema.maximum) - 1
    elif schema.exclusive_maximum is not None:
        maximum = math.ceil(schema.exclusive_maximum) - 1
    return minimum, max(minimum, maximum)


def _number_bounds(schema: SchemaSnapshot) -> tuple[float, float]:
    minimum = float(schema.minimum) if schema.minimum is not None else 0.0
    maximum = float(schema.maximum) if schema.maximum is not None else minimum + 100.0
    if not isinstance(schema.exclusive_minimum, bool) and schema.exclusive_minimum is not None:
        minimum = math.nextafter(float(schema.exclusive_minimum), math.inf)
    if not isinstance(schema.exclusive_maximum, bool) and schema.exclusive_maximum is not None:
        maximum = math.nextafter(float(schema.exclusive_maximum), -math.inf)
    return minimum, max(minimum, maximum)


def _schema_unsupported_reasons(
    node: InputNodeSnapshot,
) -> list[GeneratorDisabledReason]:
    """
    Handle schema unsupported reasons as part of deterministic request generation,
    constraint solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    schema = node.schema_contract
    assert schema is not None
    if schema.enum is not None:
        return []
    reasons: list[GeneratorDisabledReason] = []
    if schema.has_not or schema.has_conditional:
        reasons.append(
            GeneratorDisabledReason(
                code="request_schema_unsupported",
                message=f"Unsupported conditional/not schema at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    if sum(bool(items) for items in (schema.all_of, schema.any_of, schema.one_of)) > 1:
        reasons.append(
            GeneratorDisabledReason(
                code="request_schema_unsupported",
                message=f"Mixed schema combiners at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    if (schema.all_of or schema.any_of or schema.one_of) and (
        schema.properties
        or schema.required
        or schema.items is not None
        or schema.min_properties is not None
        or schema.max_properties is not None
        or schema.min_items is not None
        or schema.max_items is not None
        or schema.unique_items
    ):
        reasons.append(
            GeneratorDisabledReason(
                code="request_schema_unsupported",
                message=f"Schema combiner has unsupported sibling constraints at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    if schema.all_of and any(
        not _has_type(branch, "object")
        and not branch.properties
        and not branch.all_of
        for branch in schema.all_of
    ):
        reasons.append(
            GeneratorDisabledReason(
                code="request_schema_unsupported",
                message=f"allOf contains a non-object branch at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    if schema.pattern and not (
        schema.has_const or schema.has_default or schema.has_example or schema.enum
    ):
        reasons.append(
            GeneratorDisabledReason(
                code="default_generator_unavailable",
                message=f"Pattern requires an explicit generator at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    if schema.multiple_of and not (
        schema.has_const or schema.has_default or schema.has_example or schema.enum
    ):
        reasons.append(
            GeneratorDisabledReason(
                code="default_generator_unavailable",
                message=f"multipleOf requires an explicit generator at {node.canonical_path}",
                recoverable=True,
                input_node_id=node.input_node_id,
            )
        )
    return reasons


def _parameter_unsupported_reason(
    parameter: ParameterIR,
) -> GeneratorDisabledReason | None:
    """
    Handle parameter unsupported reason as part of deterministic request generation,
    constraint solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if parameter.content:
        return GeneratorDisabledReason(
            code="request_parameter_unsupported",
            message=f"Parameter content is unsupported: {parameter.location}/{parameter.name}",
        )
    supported_styles = {
        "path": {None, "simple", "label", "matrix"},
        "query": {None, "form", "deepObject", "spaceDelimited", "pipeDelimited"},
        "header": {None, "simple"},
        "cookie": {None, "form"},
    }
    collection_format = parameter.raw.get("collectionFormat")
    deep_object_unsupported = (
        parameter.style == "deepObject"
        and (
            parameter.location != "query"
            or parameter.explode is not True
            or parameter.schema is None
            or not _ir_is_object_contract(parameter.schema)
        )
    )
    if parameter.style not in supported_styles.get(parameter.location, set()) or (
        collection_format is not None
        and collection_format not in {"csv", "ssv", "tsv", "pipes", "multi"}
    ) or deep_object_unsupported:
        return GeneratorDisabledReason(
            code="request_parameter_unsupported",
            message=f"Unsupported parameter serialization: {parameter.location}/{parameter.name}",
        )
    return None


def _node_is_required(
    operation: OperationIR,
    canonical_path: str,
    parent_node_id: str | None,
) -> bool:
    """
    Handle node is required as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    parameter = next(
        (
            item
            for item in _parameters(operation)
            if _parameter_path(item) == canonical_path
        ),
        None,
    )
    if parameter is not None:
        return parameter.required
    if canonical_path == "body":
        return bool(operation.request_body and operation.request_body.required)
    if parent_node_id is None:
        return False
    parent = operation.input_nodes[parent_node_id]
    marker = f"{parent.canonical_path}/properties/"
    if canonical_path.startswith(marker):
        name = _unsegment(canonical_path.removeprefix(marker).split("/", 1)[0])
        return parent.schema is not None and name in parent.schema.required
    return True


def _parameters(operation: OperationIR) -> tuple[ParameterIR, ...]:
    return (
        *operation.path_parameters,
        *operation.query_parameters,
        *operation.header_parameters,
        *operation.cookie_parameters,
    )


def _has_type(schema: SchemaSnapshot, expected: str) -> bool:
    return (
        expected in schema.type
        if isinstance(schema.type, list)
        else schema.type == expected
    )


def _parameter_path(parameter: ParameterIR) -> str:
    name = parameter.name.lower() if parameter.location == "header" else parameter.name
    return f"{parameter.location}/{_segment(name)}"


def _is_supported_media_contract(
    value: str,
    *,
    schema: SchemaIR | None,
    encoding: dict[str, object],
) -> bool:
    if (
        schema is None
        or encoding
        or schema.format in {"binary", "byte"}
        or "*" in value
    ):
        return False
    if value == "application/json" or value.endswith("+json"):
        return True
    if value == "application/x-www-form-urlencoded":
        return _ir_has_type(schema, "object") or bool(schema.properties)
    if value.startswith("text/"):
        return _ir_has_type(schema, "string")
    return False


def _ir_is_object_contract(schema: SchemaIR) -> bool:
    if _ir_has_type(schema, "object") or bool(schema.properties):
        return True
    branches = schema.all_of or schema.any_of or schema.one_of
    return bool(branches) and all(
        _ir_is_object_contract(branch)
        for branch in branches
    )


def _media_type_priority(value: str) -> tuple[int, str]:
    if value == "application/json":
        return 0, value
    if value.endswith("+json"):
        return 1, value
    if value == "application/x-www-form-urlencoded":
        return 2, value
    return 3, value


def _deduplicate_reasons(
    reasons: list[GeneratorDisabledReason],
) -> list[GeneratorDisabledReason]:
    return list(
        {
            (reason.code, reason.message, reason.recoverable): reason
            for reason in reasons
        }.values()
    )


def _selected_node_ids(
    snapshot: OperationTestSnapshot,
    active_media_type: str | None,
) -> set[str]:
    """
    Handle selected node ids as part of deterministic request generation, constraint
    solving, and execution.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    active_root_id = (
        snapshot.media_type_node_ids.get(active_media_type)
        if active_media_type is not None
        else None
    )
    active_root = next(
        (
            node
            for node in snapshot.input_nodes
            if node.input_node_id == active_root_id
        ),
        None,
    )
    selected = {
        node.input_node_id
        for node in snapshot.input_nodes
        if not node.canonical_path.startswith("body/")
        or (
            active_root is not None
            and (
                node.input_node_id == active_root.input_node_id
                or node.canonical_path.startswith(
                    f"{active_root.canonical_path}/"
                )
            )
        )
    }
    nodes = {node.input_node_id: node for node in snapshot.input_nodes}
    return {
        node_id
        for node_id in selected
        if not _has_enum_ancestor(nodes[node_id], nodes)
    }


def _has_enum_ancestor(
    node: InputNodeSnapshot,
    nodes: dict[str, InputNodeSnapshot],
) -> bool:
    parent_id = node.parent_node_id
    while parent_id is not None:
        parent = nodes[parent_id]
        if (
            parent.schema_contract is not None
            and parent.schema_contract.enum is not None
        ):
            return True
        parent_id = parent.parent_node_id
    return False


def _ir_has_type(schema: SchemaIR, expected: str) -> bool:
    return (
        expected in schema.type
        if isinstance(schema.type, list)
        else schema.type == expected
    )


def _segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _unsegment(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")
