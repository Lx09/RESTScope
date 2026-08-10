"""SQLAlchemy persistence adapter for RESTScope."""

from .base import Base
from .bootstrap import (
    DatabaseAlreadyExistsError,
    DatabaseBootstrapError,
    UnsupportedDatabaseURLError,
)
from .session import create_engine_from_config, create_engine_from_url, make_session_factory
from .adapters import (
    SqlAlchemyResourceCatalogUnitOfWork,
    SqlAlchemyResponseValueCatalogUnitOfWork,
    SqlAlchemyOpenAPIUnitOfWork,
)

__all__ = [
    "Base",
    "DatabaseAlreadyExistsError",
    "DatabaseBootstrapError",
    "SqlAlchemyOpenAPIUnitOfWork",
    "SqlAlchemyResourceCatalogUnitOfWork",
    "SqlAlchemyResponseValueCatalogUnitOfWork",
    "UnsupportedDatabaseURLError",
    "create_engine_from_config",
    "create_engine_from_url",
    "make_session_factory",
]
