"""Constraint tags builder for OpenAPI specs."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ir import OpenAPISpecIR, OperationIR, SchemaIR


def _extract_schema_tags(schema: "SchemaIR") -> list[str]:
    """
    Extract constraint tags from a schema.

    Args:
        schema: The SchemaIR instance.

    Returns:
        List of tag names.
    """
    tags = []

    if schema.enum is not None:
        tags.append("has_enum")

    if schema.pattern is not None:
        tags.append("has_pattern")

    if schema.minimum is not None or schema.maximum is not None:
        tags.append("has_numeric_range")

    if schema.min_length is not None or schema.max_length is not None:
        tags.append("has_length_constraint")

    if schema.const is not None:
        tags.append("has_const")

    if schema.unique_items is not None:
        tags.append("has_unique_items")

    if schema.additional_properties is not None:
        tags.append("has_additional_properties_constraint")

    # Check combiners
    if schema.all_of:
        tags.append("has_all_of")
    if schema.any_of:
        tags.append("has_any_of")
    if schema.one_of:
        tags.append("has_one_of")

    # Check array items constraints
    if schema.items:
        if (
            schema.items.min_items is not None
            or schema.items.max_items is not None
            or schema.items.unique_items is not None
        ):
            tags.append("has_array_items_constraint")

    return tags


def build_constraint_tags(ir: "OpenAPISpecIR") -> None:
    """
    Build constraint tags for operations.

    Tags indicate common constraint patterns that can be useful for test generation:
    - has_enum
    - has_pattern
    - has_numeric_range
    - has_required_params
    - has_one_of
    - has_any_of
    - has_all_of
    - has_security
    - has_json_body
    - has_form_body
    - has_file_upload
    - has_array_items_constraint

    Args:
        ir: The OpenAPISpecIR instance to update.
    """
    from ..ir import ConstraintTagIR

    tags: list[ConstraintTagIR] = []

    for op_key, op in ir.operations.items():
        # Check security
        if op.security.requirements:
            tags.append(ConstraintTagIR(op_key, "has_security", {}))

        # Collect all parameters
        all_params = (
            op.path_parameters
            + op.query_parameters
            + op.header_parameters
            + op.cookie_parameters
        )

        # Check required parameters
        if any(p.required for p in all_params):
            tags.append(ConstraintTagIR(op_key, "has_required_params", {}))

        # Extract tags from parameter schemas
        for param in all_params:
            if param.schema is not None:
                param_tags = _extract_schema_tags(param.schema)
                for tag in param_tags:
                    tags.append(ConstraintTagIR(op_key, tag, {"param": param.name}))

        # Check request body
        if op.request_body is not None:
            for media_type, media in op.request_body.contents.items():
                if media_type == "application/json":
                    tags.append(ConstraintTagIR(op_key, "has_json_body", {}))
                if media_type in {
                    "application/x-www-form-urlencoded",
                    "multipart/form-data",
                }:
                    tags.append(ConstraintTagIR(op_key, "has_form_body", {}))
                if media_type == "multipart/form-data":
                    tags.append(ConstraintTagIR(op_key, "has_file_upload", {}))

                if media.schema is not None:
                    body_tags = _extract_schema_tags(media.schema)
                    for tag in body_tags:
                        tags.append(ConstraintTagIR(op_key, tag, {"media_type": media_type}))

        # Check response schemas for constraints
        for status_code, response in op.responses.by_status.items():
            for media_type, media in response.contents.items():
                if media.schema:
                    response_tags = _extract_schema_tags(media.schema)
                    for tag in response_tags:
                        tags.append(
                            ConstraintTagIR(
                                op_key, tag, {"response": status_code, "media_type": media_type}
                            )
                        )

    ir.indexes.constraint_tags = tags
