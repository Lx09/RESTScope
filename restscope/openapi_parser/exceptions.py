"""Exception classes for the OpenAPI parser."""


class OpenAPIParserError(Exception):
    """Base exception for all OpenAPI parser errors."""


class LoaderError(OpenAPIParserError):
    """Raised when the loader fails to load the input."""


class UnsupportedSpecVersionError(OpenAPIParserError):
    """Raised when the spec version cannot be identified."""


class InvalidTopLevelSchemaError(OpenAPIParserError):
    """Raised when the top-level schema is invalid."""


class ReferenceResolutionError(OpenAPIParserError):
    """Raised when a $ref cannot be resolved."""


class RecursiveReferenceError(OpenAPIParserError):
    """Raised when a recursive reference is detected."""


class SchemaParseError(OpenAPIParserError):
    """Raised when a schema cannot be parsed."""


class OperationDocumentGenerationError(OpenAPIParserError):
    """Raised when selected operations cannot form a valid OpenAPI document."""
