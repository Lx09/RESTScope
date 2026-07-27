"""Structured output parsing and Pydantic validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from restscope.llm.schemas import LLMResponse, ValidationIssue, ValidationResult


class OutputValidator:
    """Convert an LLM response into the expected typed output model."""

    def validate(
        self,
        *,
        response: LLMResponse,
        output_model: Any,
    ) -> ValidationResult:
        """
        Handle validate as part of provider-independent language-model invocation.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        raw_json, parse_errors = self._load_json(response)
        if parse_errors:
            return ValidationResult(valid=False, errors=parse_errors)

        try:
            validated = output_model.model_validate(raw_json)
        except ValidationError as exc:
            return ValidationResult(
                valid=False,
                raw_json=raw_json,
                errors=[
                    ValidationIssue(
                        type="model_validation_failed",
                        message=error.get("msg", "Invalid structured output"),
                        location=".".join(str(part) for part in error.get("loc", ())),
                        metadata={"error_type": error.get("type")},
                    )
                    for error in exc.errors()
                ],
            )

        return ValidationResult(valid=True, validated_object=validated, raw_json=raw_json)

    def _load_json(self, response: LLMResponse) -> tuple[Any | None, list[ValidationIssue]]:
        if response.parsed_json is not None:
            return response.parsed_json, []
        if not response.content:
            return None, [ValidationIssue(type="missing_content", message="LLM response has no JSON content")]
        try:
            return json.loads(response.content), []
        except json.JSONDecodeError as exc:
            return None, [
                ValidationIssue(
                    type="json_parse_failed",
                    message=str(exc),
                    location=f"line:{exc.lineno}:column:{exc.colno}",
                )
            ]
