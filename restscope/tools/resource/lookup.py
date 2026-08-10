"""Expose bounded, read-only Resource Identifier catalog queries as tools.

The API Behavior Monitor learns canonical resource names, aliases, and typed
identifiers from successful target responses. ``ResourceToolBackend``
projects only the small discovery results a caller requests. It neither records
new evidence nor exposes operation usage, Monitor errors, or database keys.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from restscope.llm import ToolSpec
from restscope.tools.runtime import ToolBinding

if TYPE_CHECKING:
    from restscope.api_behavior_monitor.resource_identifiers import ResourceCatalog


RESOURCE_LIST_RESOURCES_TOOL_NAME = "resource.list_resources"
RESOURCE_LIST_IDS_TOOL_NAME = "resource.list_ids"

_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 200


def resource_tool_bindings(
    backend: "ResourceToolBackend | None",
    *,
    unavailable: Callable[..., dict[str, object]],
) -> tuple[ToolBinding, ...]:
    """Bind canonical resource and identifier reads to one Monitor Catalog."""
    return (
        ToolBinding(
            name=RESOURCE_LIST_RESOURCES_TOOL_NAME,
            execute=(backend.list_resources if backend is not None else unavailable),
        ),
        ToolBinding(
            name=RESOURCE_LIST_IDS_TOOL_NAME,
            execute=(backend.list_ids if backend is not None else unavailable),
        ),
    )


def resource_list_resources_tool_spec() -> ToolSpec:
    """Describe canonical resource discovery without aliases or identifiers."""
    return ToolSpec(
        name=RESOURCE_LIST_RESOURCES_TOOL_NAME,
        description=(
            "List canonical resource names learned from successful API "
            "responses. Results are paginated and contain names only."
        ),
        kind="local_function",
        input_schema=_pagination_input_schema(),
        output_schema={
            "type": "object",
            "properties": {
                "resources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": ["resources", "total", "offset"],
            "additionalProperties": False,
        },
    )


def resource_list_ids_tool_spec() -> ToolSpec:
    """Describe typed identifier discovery for one resource name or alias."""
    return ToolSpec(
        name=RESOURCE_LIST_IDS_TOOL_NAME,
        description=(
            "List reusable typed identifiers for one canonical resource name "
            "or learned alias. Results are paginated."
        ),
        kind="local_function",
        input_schema=_pagination_input_schema(
            extra_properties={
                "resource": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                }
            },
            required=["resource"],
        ),
        output_schema={
            "type": "object",
            "properties": {
                "requested_resource": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["found", "not_found"],
                },
                "canonical_resource": {"type": "string"},
                "ids": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "identifier": {"type": "string"},
                            "components": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "value": {"type": ["string", "integer"]},
                                        "value_type": {
                                            "type": "string",
                                            "enum": ["string", "integer"],
                                        },
                                    },
                                    "required": ["name", "value", "value_type"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["identifier", "components"],
                        "additionalProperties": False,
                    },
                },
                "total": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "next_offset": {"type": "integer", "minimum": 0},
            },
            "required": [
                "requested_resource",
                "status",
                "ids",
                "total",
                "offset",
            ],
            "additionalProperties": False,
        },
    )


class ResourceToolBackend:
    """Answer compact queries against the current Resource Identifier Catalog.

    Args:
        catalog: The API Behavior Monitor Catalog whose transaction Interface
            owns all database reads.

    Methods return tool-shaped structured payloads and never change Catalog
    state. A workflow may explicitly register either method in its own toolbox;
    constructing this Backend alone does not expose a tool to an Agent.
    """

    def __init__(self, *, catalog: ResourceCatalog) -> None:
        """Retain the existing Catalog without opening a database transaction."""
        self._catalog = catalog

    def list_resources(
        self,
        *,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return one stable page of canonical resource names.

        Args:
            offset: Number of alphabetical names to skip.
            limit: Maximum names in the returned page. Tool validation enforces
                the public upper bound before this method runs.

        Returns:
            A structured tool payload with page metadata. An offset beyond the
            end succeeds with an empty ``resources`` list.
        """
        names, total = self._catalog.list_resource_names(
            offset=offset,
            limit=limit,
        )
        result: dict[str, object] = {
            "resources": [{"name": name} for name in names],
            "total": total,
            "offset": offset,
        }
        next_offset = offset + len(names)
        if next_offset < total:
            result["next_offset"] = next_offset
        return {"structured": result}

    def list_ids(
        self,
        *,
        resource: str,
        offset: int = 0,
        limit: int = _DEFAULT_LIST_LIMIT,
    ) -> dict[str, object]:
        """Return one typed-ID page for a canonical resource name or alias.

        Args:
            resource: Name resolved through the Catalog's canonical alias map.
            offset: Number of identifiers in recency order to skip.
            limit: Maximum identifiers to include.

        Returns:
            A structured found result, or a successful ``not_found`` result
            with no identifiers. Raw database identities and timestamps never
            enter the tool payload.
        """
        page = self._catalog.list_identifiers(
            resource=resource,
            offset=offset,
            limit=limit,
        )
        result: dict[str, object] = {
            "requested_resource": resource,
            "status": page.status,
            **(
                {"canonical_resource": page.canonical_resource}
                if page.canonical_resource is not None
                else {}
            ),
            "ids": [
                {
                    "identifier": item.identifier,
                    "components": [
                        component.model_dump(mode="json")
                        for component in item.components
                    ],
                }
                for item in page.identifiers
            ],
            "total": page.total,
            "offset": offset,
        }
        next_offset = offset + len(page.identifiers)
        if next_offset < page.total:
            result["next_offset"] = next_offset
        return {"structured": result}


def _pagination_input_schema(
    *,
    extra_properties: dict[str, object] | None = None,
    required: list[str] | None = None,
) -> dict[str, object]:
    """Build the identical bounded pagination contract used by Resource tools."""
    return {
        "type": "object",
        "properties": {
            **(extra_properties or {}),
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_LIST_LIMIT,
                "default": _DEFAULT_LIST_LIMIT,
            },
        },
        "required": required or [],
        "additionalProperties": False,
    }
