"""Meta parser module for parsing specification metadata."""

from ..adapters.base import SpecificationAdapter
from ..constants import SPEC_FORMAT_SWAGGER2
from ..ir import ServerIR, SpecMetaIR
from .server_parser import parse_servers


def _normalize_swagger2_base_path(base_path: str | None) -> str:
    """Normalize Swagger 2.0 basePath for URL assembly."""
    if not base_path:
        return ""
    if base_path == "/":
        return ""
    return base_path if base_path.startswith("/") else f"/{base_path}"


def _select_swagger2_scheme(schemes: list[str]) -> str:
    """Select a default Swagger 2.0 scheme, preferring http."""
    normalized = [scheme.strip() for scheme in schemes if scheme and scheme.strip()]
    if not normalized:
        return "http"
    if "http" in normalized:
        return "http"
    return normalized[0]


def _build_swagger2_default_server(
    raw_schema: dict,
    adapter: SpecificationAdapter,
    spec_format: str,
) -> list[ServerIR]:
    """Build a synthetic global server from Swagger 2.0 host metadata."""
    if spec_format != SPEC_FORMAT_SWAGGER2:
        return []

    host = adapter.get_host(raw_schema)
    if not host:
        return []

    base_path = _normalize_swagger2_base_path(adapter.get_base_path(raw_schema))
    scheme = _select_swagger2_scheme(adapter.get_schemes(raw_schema))
    return [
        ServerIR(
            url=f"{scheme}://{host}{base_path}",
            description="Derived from Swagger 2.0 host/basePath/schemes",
            variables={},
        )
    ]


def parse_meta(
    raw_schema: dict,
    adapter: SpecificationAdapter,
    spec_format: str,
    spec_version: str,
) -> SpecMetaIR:
    """
    Parse specification metadata.

    Args:
        raw_schema: The raw schema dictionary.
        adapter: The specification adapter.
        spec_format: The spec format identifier.
        spec_version: The spec version string.

    Returns:
        A SpecMetaIR instance.
    """
    info = raw_schema.get("info", {})
    if not isinstance(info, dict):
        info = {}

    # Parse contact
    contact = info.get("contact")
    if not isinstance(contact, dict):
        contact = None

    # Parse license
    license_info = info.get("license")
    if not isinstance(license_info, dict):
        license_info = None

    # Parse external docs
    external_docs = info.get("externalDocs")
    if not isinstance(external_docs, dict):
        external_docs = None

    # Get base path (Swagger 2.0 only)
    base_path = adapter.get_base_path(raw_schema)

    # Get servers
    servers_raw = adapter.get_global_servers(raw_schema)
    servers = parse_servers(servers_raw)
    if not servers:
        servers = _build_swagger2_default_server(raw_schema, adapter, spec_format)

    return SpecMetaIR(
        spec_format=spec_format,
        spec_version=spec_version,
        title=info.get("title"),
        version=info.get("version"),
        description=info.get("description"),
        summary=info.get("summary"),
        terms_of_service=info.get("termsOfService"),
        contact=contact,
        license=license_info,
        external_docs=external_docs,
        base_path=base_path,
        servers=servers,
    )
