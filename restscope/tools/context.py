"""App-bound runtime context available to trusted tool handlers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType

from restscope.openapi_parser import OpenAPISpecIR


class ToolContextError(RuntimeError):
    """Stable lifecycle error for app-bound tool context."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolContext:
    """One initialized OpenAPI target shared for the lifetime of an app."""

    ir: OpenAPISpecIR
    baseline_schema_source: Mapping[str, object]
    base_url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "baseline_schema_source",
            MappingProxyType(deepcopy(dict(self.baseline_schema_source))),
        )
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))
