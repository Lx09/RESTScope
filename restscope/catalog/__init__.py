"""Database-independent OpenAPI schema catalog contracts."""

from .models import SchemaRecord, SchemaSourceInput
from .ports import SchemaRepository, SchemaUnitOfWork, SchemaUnitOfWorkFactory
from .service import SchemaCatalog, SchemaNotFoundError, SchemaSourceValidationError

__all__ = [
    "SchemaCatalog",
    "SchemaNotFoundError",
    "SchemaRecord",
    "SchemaRepository",
    "SchemaSourceInput",
    "SchemaSourceValidationError",
    "SchemaUnitOfWork",
    "SchemaUnitOfWorkFactory",
]
