"""OpenAPI Parser - A parser for Swagger 2.0 and OpenAPI 3.x specifications."""

from .document_builder import build_openapi_document
from .exceptions import OperationDocumentGenerationError
from .ir import OpenAPISpecIR
from .parser import OpenAPIParser

__all__ = [
    "OpenAPIParser",
    "OpenAPISpecIR",
    "OperationDocumentGenerationError",
    "build_openapi_document",
]
