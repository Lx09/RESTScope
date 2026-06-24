"""Schema parser module."""

from typing import Any

from ..diagnostics import make_diagnostic
from ..exceptions import RecursiveReferenceError, SchemaParseError
from ..ir import DiagnosticsIR, SchemaIR
from ..resolver import ReferenceResolver


def parse_schema(
    raw_schema_node: dict | bool | None,
    resolver: ReferenceResolver | None,
    scope: str | None,
    diagnostics: DiagnosticsIR,
    pointer: str | None = None,
    visited_refs: set[str] | None = None,
) -> SchemaIR | None:
    """
    Parse a schema node into SchemaIR.

    Args:
        raw_schema_node: The raw schema node (dict, bool, or None).
        resolver: The reference resolver for $ref resolution.
        scope: The current resolution scope.
        diagnostics: The diagnostics container.
        pointer: The JSON Pointer to this schema node.
        visited_refs: Set of already visited $ref paths to detect circular references.

    Returns:
        A SchemaIR instance or None if the schema is null.
    """
    if raw_schema_node is None:
        return None

    # Initialize visited_refs for the top-level call
    if visited_refs is None:
        visited_refs = set()

    if isinstance(raw_schema_node, bool):
        # Boolean schema
        return SchemaIR(
            type=None,
            format=None,
            title=None,
            description=None,
            properties={},
            required=[],
            items=None,
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
            source_pointer=pointer,
            raw={"__bool_schema__": raw_schema_node},
        )

    if not isinstance(raw_schema_node, dict):
        raise SchemaParseError(f"Schema node must be object, boolean or null, got {type(raw_schema_node)}")

    # Handle $ref
    if "$ref" in raw_schema_node:
        if resolver is None:
            raise SchemaParseError("Resolver is required for $ref schema")
        ref_path = raw_schema_node["$ref"]

        # Check for circular reference using our own tracking
        if ref_path in visited_refs:
            # Circular reference detected - return a placeholder schema with the $ref preserved
            return SchemaIR(
                type=None,
                format=None,
                title=None,
                description=None,
                properties={},
                required=[],
                items=None,
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
                source_pointer=pointer,
                raw={"$ref": ref_path},
                ref_path=ref_path,
            )

        # Add to visited refs and resolve
        visited_refs = visited_refs | {ref_path}  # Create new set to avoid mutation
        try:
            _, raw_schema_node = resolver.resolve(ref_path)
            if not isinstance(raw_schema_node, dict):
                raise SchemaParseError("Resolved schema is not an object")
        except RecursiveReferenceError:
            # Resolver detected circular reference - return placeholder
            return SchemaIR(
                type=None,
                format=None,
                title=None,
                description=None,
                properties={},
                required=[],
                items=None,
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
                source_pointer=pointer,
                raw={"$ref": ref_path},
                ref_path=ref_path,
            )

    # Parse properties recursively
    properties = {}
    for name, sub_schema in raw_schema_node.get("properties", {}).items():
        properties[name] = parse_schema(sub_schema, resolver, scope, diagnostics, pointer, visited_refs)
    # Filter out None values
    properties = {k: v for k, v in properties.items() if v is not None}

    # Parse items
    items_raw = raw_schema_node.get("items")
    items = parse_schema(items_raw, resolver, scope, diagnostics, pointer, visited_refs) if items_raw else None

    # Parse combiners
    all_of = [
        parse_schema(x, resolver, scope, diagnostics, pointer, visited_refs)
        for x in raw_schema_node.get("allOf", [])
    ]
    any_of = [
        parse_schema(x, resolver, scope, diagnostics, pointer, visited_refs)
        for x in raw_schema_node.get("anyOf", [])
    ]
    one_of = [
        parse_schema(x, resolver, scope, diagnostics, pointer, visited_refs)
        for x in raw_schema_node.get("oneOf", [])
    ]
    not_schema = parse_schema(
        raw_schema_node.get("not"), resolver, scope, diagnostics, pointer, visited_refs
    )

    # Parse additionalProperties
    additional_properties = raw_schema_node.get("additionalProperties")
    if isinstance(additional_properties, dict):
        additional_properties = parse_schema(
            additional_properties, resolver, scope, diagnostics, pointer, visited_refs
        )

    # Build SchemaIR
    explicit_type = raw_schema_node.get("type")

    # Infer type when not explicitly declared
    inferred_type = explicit_type
    if inferred_type is None:
        # Don't infer type for combiner schemas (anyOf/oneOf/allOf)
        has_combiners = bool(any_of) or bool(one_of) or bool(all_of)
        if not has_combiners:
            # Infer object type from properties
            if properties:
                inferred_type = "object"
            # Infer array type from items
            elif items is not None:
                inferred_type = "array"

    return SchemaIR(
        type=inferred_type,
        format=raw_schema_node.get("format"),
        title=raw_schema_node.get("title"),
        description=raw_schema_node.get("description"),
        properties=properties,
        required=list(raw_schema_node.get("required", [])),
        items=items,
        enum=raw_schema_node.get("enum"),
        const=raw_schema_node.get("const"),
        default=raw_schema_node.get("default"),
        nullable=raw_schema_node.get("nullable"),
        read_only=raw_schema_node.get("readOnly"),
        write_only=raw_schema_node.get("writeOnly"),
        deprecated=raw_schema_node.get("deprecated"),
        minimum=raw_schema_node.get("minimum"),
        maximum=raw_schema_node.get("maximum"),
        exclusive_minimum=raw_schema_node.get("exclusiveMinimum"),
        exclusive_maximum=raw_schema_node.get("exclusiveMaximum"),
        min_length=raw_schema_node.get("minLength"),
        max_length=raw_schema_node.get("maxLength"),
        pattern=raw_schema_node.get("pattern"),
        min_items=raw_schema_node.get("minItems"),
        max_items=raw_schema_node.get("maxItems"),
        unique_items=raw_schema_node.get("uniqueItems"),
        min_properties=raw_schema_node.get("minProperties"),
        max_properties=raw_schema_node.get("maxProperties"),
        all_of=[x for x in all_of if x is not None],
        any_of=[x for x in any_of if x is not None],
        one_of=[x for x in one_of if x is not None],
        not_schema=not_schema,
        additional_properties=additional_properties,
        example=raw_schema_node.get("example"),
        examples=(
            list(raw_schema_node.get("examples", []))
            if isinstance(raw_schema_node.get("examples"), list)
            else []
        ),
        discriminator=raw_schema_node.get("discriminator"),
        xml=raw_schema_node.get("xml"),
        external_docs=raw_schema_node.get("externalDocs"),
        source_pointer=pointer,
        raw=raw_schema_node,
    )