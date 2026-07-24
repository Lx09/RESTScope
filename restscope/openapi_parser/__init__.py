"""OpenAPI Parser - A parser for Swagger 2.0 and OpenAPI 3.x specifications."""

from .document_builder import build_openapi_document
from .exceptions import OperationDocumentGenerationError
from .ir import InputNodeIR, InputNodeKind, OpenAPISpecIR, OperationIR
from .operation_matching import OpenAPIOperationMatchError, match_operation
from .parser import OpenAPIParser

__all__ = [
    "OpenAPIParser",
    "OpenAPISpecIR",
    "InputNodeIR",
    "InputNodeKind",
    "OperationIR",
    "OpenAPIOperationMatchError",
    "OperationDocumentGenerationError",
    "build_openapi_document",
    "match_operation",
]
