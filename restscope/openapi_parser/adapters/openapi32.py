"""OpenAPI 3.2 adapter."""

from __future__ import annotations

from ..constants import SPEC_FORMAT_OAS32
from ..exceptions import InvalidTopLevelSchemaError
from .base import SpecificationAdapter


class OpenAPI32Adapter(SpecificationAdapter):
    """Adapter for OpenAPI 3.2 specifications."""

    @property
    def spec_format(self) -> str:
        """Return the OpenAPI 3.2 format identifier handled by this adapter."""
        return SPEC_FORMAT_OAS32

    def get_base_path(self, raw_schema: dict) -> str | None:
        """OpenAPI 3.2 does not have basePath. Returns None."""
        return None

    def get_global_servers(self, raw_schema: dict) -> list[dict]:
        """Get servers from OpenAPI 3.2 schema."""
        return raw_schema.get("servers", [])

    def get_path_item_servers(self, path_item: dict) -> list[dict]:
        """Get servers from path item."""
        return path_item.get("servers", [])

    def get_operation_servers(self, operation: dict) -> list[dict]:
        """Get servers from operation."""
        return operation.get("servers", [])

    def get_components_container(self, raw_schema: dict) -> dict:
        """Get components from OpenAPI 3.2 schema."""
        return raw_schema.get("components", {})

    def get_security_schemes_container(self, raw_schema: dict) -> dict:
        """Get securitySchemes from components."""
        components = raw_schema.get("components", {})
        return components.get("securitySchemes", {})

    def get_global_security_requirements(self, raw_schema: dict) -> list[dict]:
        """Get global security from OpenAPI 3.2 schema."""
        return raw_schema.get("security", [])

    def iter_operation_parameters(
        self,
        operation: dict,
        shared_parameters: list[dict],
    ) -> list[dict]:
        """Get operation parameters (OpenAPI 3.2 does not have body parameters)."""
        op_params = operation.get("parameters", [])
        return list(shared_parameters) + list(op_params)

    def get_request_body_definition(self, operation: dict) -> dict | None:
        """Get requestBody from OpenAPI 3.2 operation."""
        return operation.get("requestBody")

    def get_responses_definition(self, operation: dict) -> dict:
        """Get responses from OpenAPI 3.2 operation."""
        return operation.get("responses", {})

    def build_synthetic_path_parameter(self, name: str) -> dict:
        """Build a synthetic path parameter for OpenAPI 3.2."""
        return {
            "name": name,
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "synthetic": True,
        }

    def validate_top_level(self, raw_schema: dict) -> None:
        """Validate OpenAPI 3.2 top-level structure."""
        if not isinstance(raw_schema, dict):
            raise InvalidTopLevelSchemaError("Top-level schema must be an object")

        if "openapi" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'openapi' field")

        if "info" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'info' field")

        if not isinstance(raw_schema.get("info"), dict):
            raise InvalidTopLevelSchemaError("'info' must be an object")

        # paths is required in OpenAPI 3.2
        if "paths" not in raw_schema:
            raise InvalidTopLevelSchemaError("Missing 'paths' field")

        if not isinstance(raw_schema.get("paths"), dict):
            raise InvalidTopLevelSchemaError("'paths' must be an object")
