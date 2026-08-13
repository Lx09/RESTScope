"""Schema sync utilities for keeping OpenAPI IR consistent with actual responses.

Provides functions to:
- infer_schema_from_value: Create SchemaIR from actual JSON value
- schema_matches: Check if inferred schema is compatible with existing
- merge_schemas: Merge inferred schema into existing, adding missing fields

Author: lixin
"""

from __future__ import annotations

from ..ir import SchemaIR


def infer_schema_from_value(value: object) -> SchemaIR:
    """Create SchemaIR from an actual JSON value.

    Infers basic type, properties for objects, items for arrays.
    Does not infer complex constraints (min/max, pattern, etc.).

    Args:
        value: A JSON-serializable value (dict, list, str, int, etc.)

    Returns:
        SchemaIR representing the inferred schema.
    """
    if value is None:
        return _create_schema(type="null")

    if isinstance(value, bool):
        return _create_schema(type="boolean")

    if isinstance(value, int):
        return _create_schema(type="integer")

    if isinstance(value, float):
        return _create_schema(type="number")

    if isinstance(value, str):
        return _create_schema(type="string")

    if isinstance(value, list):
        items_schema = _infer_array_items(value)
        return _create_schema(type="array", items=items_schema)

    if isinstance(value, dict):
        properties = {
            k: infer_schema_from_value(v) for k, v in value.items()
        }
        required = list(value.keys())
        return _create_schema(
            type="object",
            properties=properties,
            required=required,
        )

    # Unknown type
    return _create_schema(type=None)


def _infer_array_items(values: list[object]) -> SchemaIR:
    """Infer items schema from array values.

    If array is empty, returns schema with type=None.
    If all items have same type, returns that type schema.
    If mixed types, returns oneOf with all unique type schemas.
    """
    if not values:
        return _create_schema(type=None)

    # Infer schema for each item
    item_schemas = [infer_schema_from_value(v) for v in values]

    # Check if all have same type
    unique_types = {schema.type for schema in item_schemas if schema.type}
    if len(unique_types) == 1:
        # All same type, return first non-null schema
        for s in item_schemas:
            if s.type:
                return s

    # Mixed types, could return oneOf but for simplicity return generic
    # In practice, we might want to use anyOf or just accept any
    return _create_schema(type=None)


def _create_schema(
    type: str | None,
    properties: dict[str, SchemaIR] | None = None,
    required: list[str] | None = None,
    items: SchemaIR | None = None,
) -> SchemaIR:
    """Create a minimal SchemaIR with defaults."""
    return SchemaIR(
        type=type,
        format=None,
        title=None,
        description=None,
        properties=properties or {},
        required=required or [],
        items=items,
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
        ref_path=None,
    )


def schema_matches(inferred: SchemaIR, existing: SchemaIR) -> bool:
    """Check if inferred schema is compatible with existing schema.

    Checks:
    - Type must match (or existing.type includes inferred.type)
    - For objects: all inferred.required must exist in existing.properties

    Args:
        inferred: Schema inferred from actual value
        existing: Existing schema in OpenAPI IR

    Returns:
        True if compatible, False if needs update.
    """
    # Null type can't match
    if inferred.type is None:
        return True  # Accept unknown as compatible

    # Type must match
    if existing.type is None:
        return False  # Existing has no type, needs update

    if isinstance(existing.type, list):
        if inferred.type not in existing.type:
            return False
    else:
        if inferred.type != existing.type:
            return False

    # Object type: check required fields exist
    if inferred.type == "object":
        for field in inferred.required:
            if field not in existing.properties:
                return False

    # Array type: check items
    if (
        inferred.type == "array"
        and inferred.items
        and existing.items
    ):
        return schema_matches(inferred.items, existing.items)

    return True


def merge_schemas(existing: SchemaIR, inferred: SchemaIR) -> SchemaIR:
    """Merge inferred schema into existing, adding missing fields.

    For objects: adds inferred.properties that don't exist in existing.
    For arrays: if items incompatible, keeps existing.items.

    Args:
        existing: Existing schema in OpenAPI IR
        inferred: Schema inferred from actual value

    Returns:
        Updated SchemaIR (mutates existing fields).
    """
    # If existing has no type, use inferred type
    if existing.type is None and inferred.type:
        existing.type = inferred.type

    # Object: merge properties
    if inferred.type == "object":
        for name, prop_schema in inferred.properties.items():
            if name not in existing.properties:
                existing.properties[name] = prop_schema
            else:
                # Recursively merge nested schemas
                existing.properties[name] = merge_schemas(
                    existing.properties[name],
                    prop_schema,
                )

        # Add missing required fields
        for field in inferred.required:
            if field not in existing.required:
                existing.required.append(field)

    # Array: merge items if both have items
    if inferred.type == "array" and inferred.items and existing.items:
        existing.items = merge_schemas(existing.items, inferred.items)

    return existing
