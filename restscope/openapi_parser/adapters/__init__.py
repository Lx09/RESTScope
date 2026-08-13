"""Adapters for different OpenAPI specification versions."""

from .base import SpecificationAdapter
from .openapi30 import OpenAPI30Adapter
from .openapi31 import OpenAPI31Adapter
from .openapi32 import OpenAPI32Adapter
from .swagger2 import Swagger2Adapter

__all__ = [
    "OpenAPI30Adapter",
    "OpenAPI31Adapter",
    "OpenAPI32Adapter",
    "SpecificationAdapter",
    "Swagger2Adapter",
]
