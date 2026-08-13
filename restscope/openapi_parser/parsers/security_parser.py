"""Security parser module."""

from ..adapters.base import SpecificationAdapter
from ..ir import (
    DiagnosticsIR,
    OperationSecurityIR,
    SecurityRequirementIR,
    SecuritySchemeIR,
)
from ..resolver import ReferenceResolver


def _swagger_oauth2_flows(raw_scheme: dict) -> dict[str, object]:
    """Convert a Swagger 2 OAuth flow to the OpenAPI 3 flows shape."""
    flow_name = raw_scheme.get("flow")
    scopes = raw_scheme.get("scopes", {})
    if flow_name == "implicit":
        return {
            "implicit": {
                "authorizationUrl": raw_scheme.get("authorizationUrl", ""),
                "scopes": scopes,
            }
        }
    if flow_name == "password":
        return {
            "password": {
                "tokenUrl": raw_scheme.get("tokenUrl", ""),
                "scopes": scopes,
            }
        }
    if flow_name == "application":
        return {
            "clientCredentials": {
                "tokenUrl": raw_scheme.get("tokenUrl", ""),
                "scopes": scopes,
            }
        }
    if flow_name == "accessCode":
        return {
            "authorizationCode": {
                "authorizationUrl": raw_scheme.get("authorizationUrl", ""),
                "tokenUrl": raw_scheme.get("tokenUrl", ""),
                "scopes": scopes,
            }
        }
    return {}


def parse_security_scheme(
    name: str,
    raw_scheme: dict,
    *,
    spec_format: str | None = None,
) -> SecuritySchemeIR:
    """
    Parse a security scheme definition.

    Args:
        name: The security scheme name.
        raw_scheme: The raw security scheme dictionary.

    Returns:
        A SecuritySchemeIR instance.
    """
    if not isinstance(raw_scheme, dict):
        return SecuritySchemeIR(
            name=name,
            type="",
            location=None,
            api_key_name=None,
            scheme=None,
            bearer_format=None,
            flows={},
            open_id_connect_url=None,
            description=None,
            raw={},
        )

    scheme_type = raw_scheme.get("type", "")
    scheme = raw_scheme.get("scheme")
    flows = raw_scheme.get("flows", {})
    if spec_format == "swagger2" and scheme_type == "basic":
        scheme_type = "http"
        scheme = "basic"
    elif spec_format == "swagger2" and scheme_type == "oauth2":
        flows = _swagger_oauth2_flows(raw_scheme)
    location = None

    # Determine location based on type
    if scheme_type == "apiKey":
        location = raw_scheme.get("in")
    elif scheme_type in ("http", "oauth2", "openIdConnect"):
        location = None

    return SecuritySchemeIR(
        name=name,
        type=scheme_type,
        location=location,
        api_key_name=raw_scheme.get("name") if scheme_type == "apiKey" else None,
        scheme=scheme,
        bearer_format=raw_scheme.get("bearerFormat"),
        flows=flows,
        open_id_connect_url=raw_scheme.get("openIdConnectUrl"),
        description=raw_scheme.get("description"),
        raw=raw_scheme,
    )


def parse_operation_security(
    raw_schema: dict,
    operation_raw: dict,
    adapter: SpecificationAdapter,
    resolver: ReferenceResolver,
    diagnostics: DiagnosticsIR,
) -> OperationSecurityIR:
    """
    Parse operation security requirements.

    Args:
        raw_schema: The raw schema dictionary.
        operation_raw: The raw operation dictionary.
        adapter: The specification adapter.
        resolver: The reference resolver.
        diagnostics: The diagnostics container.

    Returns:
        An OperationSecurityIR instance.
    """
    # Determine security requirements (operation-level overrides global)
    if "security" in operation_raw:
        requirement_defs = operation_raw.get("security", [])
    else:
        requirement_defs = adapter.get_global_security_requirements(raw_schema)

    # Get security schemes container
    schemes_container = adapter.get_security_schemes_container(raw_schema) or {}

    requirements: list[SecurityRequirementIR] = []
    requirement_sets: list[list[SecurityRequirementIR]] = []
    resolved_schemes: dict[str, SecuritySchemeIR] = {}

    for req in requirement_defs:
        if not isinstance(req, dict):
            continue

        requirement_set: list[SecurityRequirementIR] = []
        for scheme_name, scopes in req.items():
            requirement = SecurityRequirementIR(
                scheme_name=scheme_name,
                scopes=list(scopes or []),
            )
            requirements.append(requirement)
            requirement_set.append(requirement)

            # Parse security scheme if not already parsed
            if scheme_name not in resolved_schemes:
                raw_scheme = schemes_container.get(scheme_name)
                if raw_scheme is not None and isinstance(raw_scheme, dict):
                    resolved_schemes[scheme_name] = parse_security_scheme(
                        scheme_name,
                        raw_scheme,
                        spec_format=adapter.spec_format,
                    )
        requirement_sets.append(requirement_set)

    return OperationSecurityIR(
        requirements=requirements,
        requirement_sets=requirement_sets,
        resolved_schemes=resolved_schemes,
    )
