"""OpenAPI Parser - A parser for Swagger 2.0 and OpenAPI 3.x specifications."""

from .parser import OpenAPIParser
from .ir import OpenAPISpecIR

__all__ = ["OpenAPIParser", "OpenAPISpecIR"]
