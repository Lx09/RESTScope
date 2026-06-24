"""Adapters for different OpenAPI specification versions."""

from .base import SpecificationAdapter
from .swagger2 import Swagger2Adapter
from .openapi30 import OpenAPI30Adapter
from .openapi31 import OpenAPI31Adapter
from .openapi32 import OpenAPI32Adapter

__all__ = [
    "SpecificationAdapter",
    "Swagger2Adapter",
    "OpenAPI30Adapter",
    "OpenAPI31Adapter",
    "OpenAPI32Adapter",
]
