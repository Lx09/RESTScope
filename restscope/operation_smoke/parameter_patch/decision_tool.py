"""Describe the strict model tool used to submit one Patch proposal.

The Parameter Patch Agent offers this schema instead of asking DeepSeek to
write JSON content. Inputs are the existing semantic ``action``/``patch``
decision; output is a provider-owned tool call that the Agent validates,
compiles, and samples. This module contains no execution or persistence logic.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from restscope.llm import ToolSpec


PARAMETER_PATCH_PROPOSAL_TOOL = "submit_parameter_patch_proposal"


def parameter_patch_proposal_tool_spec() -> ToolSpec:
    """Return the single strict proposal tool offered by Parameter Patch.

    The custom schema mirrors ``ParameterPatchSubmission`` while staying inside
    DeepSeek Beta's supported JSON Schema subset. Every object branch requires
    every property it declares and rejects extra properties. Optional Python
    fields are represented by separate ``anyOf`` branches, so the existing
    wire shape keeps omission semantics instead of introducing null sentinels.

    Returns:
        A strict local-function specification. Runtime code interprets the
        arguments directly; no general Agent toolbox executes this tool.
    """
    return ToolSpec(
        name=PARAMETER_PATCH_PROPOSAL_TOOL,
        description=(
            "Submit exactly one complete replacement Parameter Patch proposal."
        ),
        kind="local_function",
        input_schema=_decision_schema(),
        strict=True,
    )


def _decision_schema() -> dict[str, Any]:
    """Build a fixed object root plus recursive strict Patch definitions."""
    definitions: dict[str, Any] = {
        "json_scalar": {
            "anyOf": [
                {"type": "null"},
                {"type": "string"},
                {"type": "integer"},
                {"type": "number"},
                {"type": "boolean"},
            ]
        },
        "json_value": {
            "anyOf": [
                {"$ref": "#/$defs/json_scalar"},
                {
                    "type": "array",
                    "items": {"$ref": "#/$defs/json_scalar"},
                },
            ]
        },
        "strategy": {"anyOf": _strategy_variants()},
        "generator_change": {"anyOf": _generator_change_variants()},
        "value_expression": {
            "anyOf": [
                _typed_object("input_value", input={"type": "string"}),
                _typed_object(
                    "literal",
                    value={"$ref": "#/$defs/json_value"},
                ),
                _typed_object(
                    "arithmetic",
                    operator=_string_enum("+", "-", "*", "/"),
                    left={"$ref": "#/$defs/value_expression"},
                    right={"$ref": "#/$defs/value_expression"},
                ),
            ]
        },
        "boolean_expression": {"anyOf": _boolean_expression_variants()},
        "constraint_change": _strict_object(
            expression={"$ref": "#/$defs/boolean_expression"}
        ),
        "patch": {
            "anyOf": [
                _strict_object(
                    changes={
                        "type": "array",
                        "items": {"$ref": "#/$defs/generator_change"},
                    }
                ),
                _strict_object(
                    constraints={
                        "type": "array",
                        "items": {"$ref": "#/$defs/constraint_change"},
                    }
                ),
                _strict_object(
                    changes={
                        "type": "array",
                        "items": {"$ref": "#/$defs/generator_change"},
                    },
                    constraints={
                        "type": "array",
                        "items": {"$ref": "#/$defs/constraint_change"},
                    },
                ),
            ]
        },
    }
    schema = _strict_object(
        action=_string_enum("propose"),
        patch={"$ref": "#/$defs/patch"},
    )
    schema["$defs"] = definitions
    return schema


def _strategy_variants() -> list[dict[str, Any]]:
    """Describe only Generator strategies the Patch compiler can accept."""
    variants = [
        _typed_object(
            "constant",
            value={"$ref": "#/$defs/json_value"},
        ),
        _typed_object(
            "integer_range",
            minimum={"type": "integer"},
            maximum={"type": "integer"},
        ),
        _typed_object(
            "number_range",
            minimum={"type": "number"},
            maximum={"type": "number"},
        ),
        _typed_object(
            "format",
            format=_string_enum("uuid", "date", "date-time", "email"),
        ),
        _typed_object(
            "variant",
            branch_weights={"type": "array", "items": {"type": "number"}},
        ),
    ]
    variants.extend(
        _optional_typed_objects(
            "choice",
            required={
                "values": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/json_value"},
                }
            },
            optional={
                "weights": {"type": "array", "items": {"type": "number"}}
            },
        )
    )
    variants.extend(
        _optional_typed_objects(
            "random_string",
            optional={
                "min_length": {"type": "integer", "minimum": 0},
                "max_length": {"type": "integer", "minimum": 0},
                "alphabet": {"type": "string"},
            },
        )
    )
    variants.extend(
        _optional_typed_objects(
            "regex",
            required={"pattern": {"type": "string"}},
            optional={
                "min_length": {"type": "integer", "minimum": 0},
                "max_length": {"type": "integer", "minimum": 0},
            },
        )
    )
    variants.extend(
        _optional_typed_objects(
            "boolean",
            optional={
                "true_probability": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                }
            },
        )
    )
    variants.extend(
        _optional_typed_objects(
            "array",
            optional={
                "min_items": {"type": "integer", "minimum": 0},
                "max_items": {"type": "integer", "minimum": 0},
            },
        )
    )
    return variants


def _generator_change_variants() -> list[dict[str, Any]]:
    """Preserve legal omission combinations for one semantic Generator edit."""
    input_schema = {"type": "string"}
    probability = {"type": "number", "minimum": 0, "maximum": 1}
    strategy = {"$ref": "#/$defs/strategy"}
    reference = {"type": "string"}
    return [
        _strict_object(input=input_schema, inclusion_probability=probability),
        _strict_object(input=input_schema, strategy=strategy),
        _strict_object(input=input_schema, reference=reference),
        _strict_object(
            input=input_schema,
            inclusion_probability=probability,
            strategy=strategy,
        ),
        _strict_object(
            input=input_schema,
            inclusion_probability=probability,
            reference=reference,
        ),
    ]


def _boolean_expression_variants() -> list[dict[str, Any]]:
    """Build recursive semantic Constraint expressions using input handles."""
    value = {"$ref": "#/$defs/value_expression"}
    boolean = {"$ref": "#/$defs/boolean_expression"}
    boolean_array = {"type": "array", "items": boolean}
    return [
        _typed_object("present", input={"type": "string"}),
        _typed_object(
            "compare",
            operator=_string_enum("==", "!=", "<", "<=", ">", ">="),
            left=value,
            right=value,
        ),
        _typed_object(
            "matches",
            value=value,
            pattern={"type": "string"},
        ),
        _typed_object(
            "implies",
            condition=boolean,
            consequence=boolean,
        ),
        _typed_object(
            "cardinality",
            expressions=boolean_array,
            minimum={"type": "integer", "minimum": 0},
            maximum={"type": "integer", "minimum": 0},
        ),
        _typed_object("and", expressions=boolean_array),
        _typed_object("or", expressions=boolean_array),
        _typed_object("not", expression=boolean),
    ]


def _optional_typed_objects(
    strategy_type: str,
    *,
    required: dict[str, Any] | None = None,
    optional: dict[str, Any],
) -> list[dict[str, Any]]:
    """Expand optional properties into strict object branches.

    DeepSeek strict mode requires every declared property to appear in the
    object's ``required`` list. Enumerating key subsets preserves the existing
    omission-based defaults without weakening that server contract.

    Args:
        strategy_type: Generator discriminator value shared by every branch.
        required: Strategy fields that must always be present.
        optional: Fields whose omission selects existing runtime defaults.

    Returns:
        One strict object schema for every legal optional-key subset.
    """
    required_fields = dict(required or {})
    optional_names = list(optional)
    variants: list[dict[str, Any]] = []
    for size in range(len(optional_names) + 1):
        for selected in combinations(optional_names, size):
            fields = {
                **required_fields,
                **{name: optional[name] for name in selected},
            }
            variants.append(_typed_object(strategy_type, **fields))
    return variants


def _typed_object(discriminator: str, **properties: Any) -> dict[str, Any]:
    """Create one closed object branch with a fixed ``type`` value."""
    return _strict_object(type=_string_enum(discriminator), **properties)


def _string_enum(*values: str) -> dict[str, Any]:
    """Add the explicit string type required by DeepSeek's enum validator."""
    return {"type": "string", "enum": list(values)}


def _strict_object(**properties: Any) -> dict[str, Any]:
    """Create a DeepSeek-compatible object with no optional declared fields."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
