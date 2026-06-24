"""Version detection and adapter selection module."""

from typing import TYPE_CHECKING

from packaging import version

if TYPE_CHECKING:
    from .adapters.base import SpecificationAdapter

from .constants import (
    SPEC_FORMAT_OAS30,
    SPEC_FORMAT_OAS31,
    SPEC_FORMAT_OAS32,
    SPEC_FORMAT_SWAGGER2,
)
from .exceptions import UnsupportedSpecVersionError

# Version thresholds
V3_1 = version.parse("3.1")
V3_2 = version.parse("3.2")


def _get_adapter_for_version(spec_format: str) -> "SpecificationAdapter":
    """Get the appropriate adapter for a given spec format."""
    # Import adapters dynamically to avoid circular imports
    if spec_format == SPEC_FORMAT_SWAGGER2:
        from .adapters.swagger2 import Swagger2Adapter
        return Swagger2Adapter()
    elif spec_format == SPEC_FORMAT_OAS30:
        from .adapters.openapi30 import OpenAPI30Adapter
        return OpenAPI30Adapter()
    elif spec_format == SPEC_FORMAT_OAS31:
        from .adapters.openapi31 import OpenAPI31Adapter
        return OpenAPI31Adapter()
    elif spec_format == SPEC_FORMAT_OAS32:
        from .adapters.openapi32 import OpenAPI32Adapter
        return OpenAPI32Adapter()
    else:
        raise UnsupportedSpecVersionError(f"Unsupported spec format: {spec_format}")


def detect_spec_version_and_adapter(
    raw_schema: dict,
) -> tuple[str, str, "SpecificationAdapter"]:
    """
    Detect the OpenAPI/Swagger version and return the appropriate adapter.

    Args:
        raw_schema: The raw schema dictionary.

    Returns:
        A tuple of (spec_format, spec_version, adapter).

    Raises:
        UnsupportedSpecVersionError: If the version cannot be determined.
    """
    swagger_version = raw_schema.get("swagger")
    if swagger_version is not None:
        return (SPEC_FORMAT_SWAGGER2, str(swagger_version), _get_adapter_for_version(SPEC_FORMAT_SWAGGER2))

    openapi_version = raw_schema.get("openapi")
    if openapi_version is not None:
        version_str = str(openapi_version)
        parsed = version.parse(version_str)
        if parsed >= V3_2:
            return (SPEC_FORMAT_OAS32, version_str, _get_adapter_for_version(SPEC_FORMAT_OAS32))
        elif parsed >= V3_1:
            return (SPEC_FORMAT_OAS31, version_str, _get_adapter_for_version(SPEC_FORMAT_OAS31))
        else:
            return (SPEC_FORMAT_OAS30, version_str, _get_adapter_for_version(SPEC_FORMAT_OAS30))

    raise UnsupportedSpecVersionError("Unable to determine OpenAPI / Swagger version.")
