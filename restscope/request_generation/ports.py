"""Define the narrow evidence Interface used by reference-backed Generators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .models import (
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)


class ReferenceValueProvider(Protocol):
    """Resolve exact response sources into current typed runtime values."""

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> Sequence[object]: ...

    def resource_key(self, strategy: ResourceIdentifierGenerator) -> str:
        """Return the resource shared by all components of one identity."""

        ...

    def resource_records(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> Sequence[Mapping[str, object]]:
        """Return complete current states for the source's resource type."""

        ...

    def resource_identity_fields(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> Sequence[str]:
        """Return the immutable identity fields for the resolved resource."""

        ...
