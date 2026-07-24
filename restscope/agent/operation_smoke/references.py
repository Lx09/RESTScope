"""Adapters from persisted monitoring evidence to generator value pools."""

from __future__ import annotations

from restscope.agent.resource_monitor import (
    ResourceCatalog,
    ResourceLookupRequest,
)
from restscope.testing import (
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)


class ResourceCatalogReferenceValues:
    """Resolve resource IDs; generic response-value pools remain fail-closed."""

    def __init__(self, catalog: ResourceCatalog) -> None:
        self.catalog = catalog

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> list[object]:
        if isinstance(strategy, ResponseValueGenerator):
            return []
        result = self.catalog.lookup(
            ResourceLookupRequest(
                resource=strategy.resource,
                limit=100,
            )
        )
        return [item.value for item in result.identifiers]
