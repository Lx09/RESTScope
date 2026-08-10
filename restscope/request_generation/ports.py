"""Define the narrow evidence Interface used by reference-backed Generators."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .models import ResourceIdentifierGenerator, ResponseValueGenerator


class ReferenceValueProvider(Protocol):
    """Return current typed values for one resource or response-value pool."""

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]: ...


class ObservedResponseFieldLookup(Protocol):
    """Expose only the observed-field lookup needed during Patch validation."""

    def find_observed_response_fields(
        self,
        *,
        name: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]: ...


class ResourceIdentifierLookup(Protocol):
    """Expose only canonical resource identifiers needed during validation."""

    def list_ids(self, *, resource: str, limit: int) -> dict[str, Any]: ...
