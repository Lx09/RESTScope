"""Swagger 2.0 adapter."""

from __future__ import annotations

from typing import Any

from .base import SpecificationAdapter
from ..constants import SPEC_FORMAT_SWAGGER2
from ..exceptions import InvalidTopLevelSchemaError


class Swagger2Adapter(SpecificationAdapter):
    """Adapter for Swagger 2.0 specifications."""

    @property
    def spec_format(self) -> str:
        return SPEC_FORMAT_SWAGGER2

    def get_base_path(self, raw_schema: dict) -> str | None:
        """Get basePath from Swagger 2.0 schema."""
        return raw_schema.get("basePath")

    def get_host(self, raw_schema: dict) -> str | None:
        """Get host from Swagger 2.0 schema."""
        return raw_schema.get("host")

    def get_schemes(self, raw_schema: dict) -> list[str]:
        """Get schemes from Swagger 2.0 schema."""
        schemes = raw_schema.get("schemes", [])
        if not isinstance(schemes, list):
            return []
        return [str(scheme).strip() for scheme in schemes if str(scheme).strip()]

    def get_global_servers(self, raw_schema: dict) -> list[dict]:
        """Swagger 2.0 does not have servers. Returns empty list."""
        return []

    def get_path_item_servers(self, path_item: dict) -> list[dict]:
        """Swagger 2.0 does not have path item servers. Returns empty list."""
        return []

    def get_operation_servers(self, operation: dict) -> list[dict]:
        """Swagger 2.0 does not have operation servers. Returns empty list."""
        return []

    def get_components_container(self, raw_schema: dict) -> dict:
        """
        Get components from Swagger 2.0 definitions.

        Maps Swagger 2.0 top-level keys to components-style structure.
        """
        components: dict[str, Any] = {}

        # Map definitions to schemas
        if "definitions" in raw_schema:
            components["schemas"] = raw_schema.get("definitions", {})

        # Map parameters
        if "parameters" in raw_schema:
            components["parameters"] = raw_schema.get("parameters", {})

        # Map responses
        if "responses" in raw_schema:
            components["responses"] = raw_schema.get("responses", {})

        # Map securityDefinitions
        if "securityDefinitions" in raw_schema:
            components["securitySchemes"] = raw_schema.get("securityDefinitions", {})

        return components

    def get_security_schemes_container(self, raw_schema: dict) -> dict:
        """Get securityDefinitions from Swagger 2.0 schema."""
        return raw_schema.get("securityDefinitions", {})

    def get_global_security_requirements(self, raw_schema: dict) -> list[dict]:
        """Get global security from Swagger 2.0 schema."""
        return raw_schema.get("security", [])

    def iter_operation_parameters(
        self,
        operation: dict,
        shared_parameters: list[dict],
    ) -> list[dict]:
        """
        Get operation parameters.

        In Swagger 2.0, parameters can be at path level or operation level.
        Body parameters (in == "body") should be handled separately.
        """
        op_params = operation.get("parameters", [])
        return list(shared_parameters) + list(op_params)

    def get_request_body_definition(self, operation: dict) -> dict | None:
        """
        Get request body from Swagger 2.0 operation.

        In Swagger 2.0, request body is defined via parameters with in == "body"
        or in == "formData".
        """
        params = operation.get("parameters", [])
        body_params = [p for p in params if p.get("in") == "body"]
        form_params = [p for p in params if p.get("in") == "formData"]

        if body_params:
            # Use the first body parameter
            body_param = body_params[0]
            consumes = operation.get("consumes")
            if not consumes:
                consumes = ["application/json"]

            content = {}
            for media_type in consumes:
                content[media_type] = {
                    "schema": body_param.get("schema", {}),
                    "example": body_param.get("example"),
                }

            return {
                "required": body_param.get("required", False),
                "description": body_param.get("description"),
                "content": content,
                "source": "body_parameter",
            }

        if form_params:
            # Build schema from form parameters
            properties = {}
            required = []
            for p in form_params:
                prop_schema = {}
                if "type" in p:
                    prop_schema["type"] = p["type"]
                if "format" in p:
                    prop_schema["format"] = p["format"]
                if "description" in p:
                    prop_schema["description"] = p["description"]
                if "enum" in p:
                    prop_schema["enum"] = p["enum"]

                properties[p.get("name", "")] = prop_schema
                if p.get("required", False):
                    required.append(p.get("name", ""))

            consumes = operation.get("consumes", ["application/x-www-form-urlencoded"])

            content = {}
            for media_type in consumes:
                content[media_type] = {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }

            return {
                "required": False,
                "description": None,
                "content": content,
                "source": "formData",
                "form_params": form_params,
            }

        return None

    def get_responses_definition(self, operation: dict) -> dict:
        """Get responses from Swagger 2.0 operation."""
        return operation.get("responses", {})

    def build_synthetic_path_parameter(self, name: str) -> dict:
        """Build a synthetic path parameter for Swagger 2.0."""
        return {
            "name": name,
            "in": "path",
            "required": True,
            "type": "string",
            "synthetic": True,
        }

    def validate_top_level(self, raw_schema: dict) -> None:
        """Validate Swagger 2.0 top-level structure."""
        if not isinstance(raw_schema, dict):
            raise InvalidTopLevelSchemaError("Top-level schema must be an object")

        if "swagger" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'swagger' field")

        if "info" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'info' field")

        if not isinstance(raw_schema.get("info"), dict):
            raise InvalidTopLevelSchemaError("'info' must be an object")

        # paths is required in Swagger 2.0
        if "paths" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'paths' field")

        if not isinstance(raw_schema.get("paths"), dict):
            raise InvalidTopLevelSchemaError("'paths' must be an object")
