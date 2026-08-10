"""Define the narrow evidence Interface used by reference-backed Generators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .models import ResourceIdentifierGenerator, ResponseValueGenerator


class ReferenceValueProvider(Protocol):
    """Return current typed values for one resource or response-value pool."""

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]: ...

    def identifier_records(
        self,
        *,
        resource: str,
        identifier: str,
    ) -> Sequence[Mapping[str, object]]: ...


class ResourceIdentifierLookup(Protocol):
    """Expose only canonical resource identifiers needed during validation."""

    def list_ids(self, *, resource: str, limit: int) -> dict[str, object]: ...
