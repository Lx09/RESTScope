"""Base adapter interface for OpenAPI specification versions."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SpecificationAdapter(ABC):
    """
    Abstract base class for OpenAPI specification adapters.

    Each adapter handles version-specific differences between Swagger 2.0
    and various OpenAPI 3.x versions.
    """

    @property
    @abstractmethod
    def spec_format(self) -> str:
        """Return the spec format identifier."""

    @abstractmethod
    def get_base_path(self, raw_schema: dict) -> str | None:
        """
        Get the base path from the schema.

        Args:
            raw_schema: The raw schema dictionary.

        Returns:
            The base path or None.
        """

    def get_host(self, raw_schema: dict) -> str | None:
        """Get host from the schema when the format supports it."""
        return None

    def get_schemes(self, raw_schema: dict) -> list[str]:
        """Get schemes from the schema when the format supports them."""
        return []

    @abstractmethod
    def get_global_servers(self, raw_schema: dict) -> list[dict]:
        """
        Get global server definitions.

        Args:
            raw_schema: The raw schema dictionary.

        Returns:
            List of server definitions.
        """

    @abstractmethod
    def get_path_item_servers(self, path_item: dict) -> list[dict]:
        """
        Get server definitions at path item level.

        Args:
            path_item: The path item dictionary.

        Returns:
            List of server definitions.
        """

    @abstractmethod
    def get_operation_servers(self, operation: dict) -> list[dict]:
        """
        Get server definitions at operation level.

        Args:
            operation: The operation dictionary.

        Returns:
            List of server definitions.
        """

    @abstractmethod
    def get_components_container(self, raw_schema: dict) -> dict:
        """
        Get the components container.

        Args:
            raw_schema: The raw schema dictionary.

        Returns:
            The components dictionary.
        """

    @abstractmethod
    def get_security_schemes_container(self, raw_schema: dict) -> dict:
        """
        Get the security schemes container.

        Args:
            raw_schema: The raw schema dictionary.

        Returns:
            The security schemes dictionary.
        """

    @abstractmethod
    def get_global_security_requirements(self, raw_schema: dict) -> list[dict]:
        """
        Get global security requirements.

        Args:
            raw_schema: The raw schema dictionary.

        Returns:
            List of security requirement objects.
        """

    @abstractmethod
    def iter_operation_parameters(
        self,
        operation: dict,
        shared_parameters: list[dict],
    ) -> list[dict]:
        """
        Get all parameters for an operation.

        Args:
            operation: The operation dictionary.
            shared_parameters: Path-level shared parameters.

        Returns:
            List of parameter definitions.
        """

    @abstractmethod
    def get_request_body_definition(self, operation: dict) -> dict | None:
        """
        Get request body definition for an operation.

        Args:
            operation: The operation dictionary.

        Returns:
            Request body definition or None.
        """

    @abstractmethod
    def get_responses_definition(self, operation: dict) -> dict:
        """
        Get responses definition for an operation.

        Args:
            operation: The operation dictionary.

        Returns:
            Responses dictionary.
        """

    @abstractmethod
    def build_synthetic_path_parameter(self, name: str) -> dict:
        """
        Build a synthetic path parameter definition.

        Args:
            name: The path parameter name.

        Returns:
            A parameter definition dictionary.
        """

    @abstractmethod
    def validate_top_level(self, raw_schema: dict) -> None:
        """
        Validate the top-level schema structure.

        Args:
            raw_schema: The raw schema dictionary.

        Raises:
            InvalidTopLevelSchemaError: If the schema is invalid.
        """
