"""Build shared closed output Schemas for Test Case evidence Tools."""

from __future__ import annotations

from typing import Any


def _cases_schema(fact_schema: dict[str, Any]) -> dict[str, Any]:
    """Build the shared case-keyed output envelope for one fixed fact shape."""
    return {
        "type": "object",
        "properties": {
            "cases": {
                "type": "object",
                "additionalProperties": fact_schema,
            }
        },
        "required": ["cases"],
        "additionalProperties": False,
    }

def _evidence_fragment_schema(kind: str) -> dict[str, Any]:
    """Describe dynamic, direct-name JSON retained from one Test Case."""
    return {
        "type": "object",
        "description": (
            f"Bounded {kind} evidence using the target API's direct field names. "
            "Its keys are intentionally open because each OpenAPI operation has "
            "a different shape."
        ),
        "additionalProperties": True,
    }

def _parameter_fact_schema() -> dict[str, Any]:
    """Describe used and unused Parameter facts without a boolean flag."""
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "status": {"const": "parameter_used_in_request"},
                    "request": _evidence_fragment_schema("request"),
                },
                "required": ["parameter", "status", "request"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "parameter": {"type": "string"},
                    "status": {"const": "parameter_not_used_in_request"},
                },
                "required": ["parameter", "status"],
                "additionalProperties": False,
            },
        ]
    }

def _response_field_fact_schema() -> dict[str, Any]:
    """Describe the three exact response-field evidence outcomes."""
    statuses_without_value = [
        "response_body_not_retained",
        "response_field_not_present_in_retained_body",
    ]
    return {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "status": {
                        "const": "response_field_present_in_retained_body"
                    },
                    "response": _evidence_fragment_schema("response"),
                },
                "required": ["field", "status", "response"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "status": {"enum": statuses_without_value},
                },
                "required": ["field", "status"],
                "additionalProperties": False,
            },
        ]
    }
