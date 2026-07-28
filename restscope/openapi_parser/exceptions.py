"""Exception classes for the OpenAPI parser."""


class OpenAPIParserError(Exception):
    """Base exception for all OpenAPI parser errors."""
    pass


class LoaderError(OpenAPIParserError):
    """Raised when the loader fails to load the input."""
    pass


class UnsupportedSpecVersionError(OpenAPIParserError):
    """Raised when the spec version cannot be identified."""
    pass


class InvalidTopLevelSchemaError(OpenAPIParserError):
    """Raised when the top-level schema is invalid."""
    pass


class ReferenceResolutionError(OpenAPIParserError):
    """Raised when a $ref cannot be resolved."""
    pass


class RecursiveReferenceError(OpenAPIParserError):
    """Raised when a recursive reference is detected."""
    pass


class SchemaParseError(OpenAPIParserError):
    """Raised when a schema cannot be parsed."""
    pass


class OperationDocumentGenerationError(OpenAPIParserError):
    """Raised when selected operations cannot form a valid OpenAPI document."""
    pass
