"""SQLAlchemy persistence adapter for RESTScope."""

from .base import Base
from .bootstrap import (
    DatabaseAlreadyExistsError,
    DatabaseBootstrapError,
    UnsupportedDatabaseURLError,
)
from .session import create_engine_from_config, create_engine_from_url, make_session_factory
from .unit_of_work import (
    SqlAlchemyGeneratorConfigUnitOfWork,
    SqlAlchemyResourceCatalogUnitOfWork,
    SqlAlchemyResponseValueCatalogUnitOfWork,
    SqlAlchemyOpenAPIUnitOfWork,
    SqlAlchemySmokeMemoryUnitOfWork,
)

__all__ = [
    "Base",
    "DatabaseAlreadyExistsError",
    "DatabaseBootstrapError",
    "SqlAlchemyOpenAPIUnitOfWork",
    "SqlAlchemyGeneratorConfigUnitOfWork",
    "SqlAlchemyResourceCatalogUnitOfWork",
    "SqlAlchemyResponseValueCatalogUnitOfWork",
    "SqlAlchemySmokeMemoryUnitOfWork",
    "UnsupportedDatabaseURLError",
    "create_engine_from_config",
    "create_engine_from_url",
    "make_session_factory",
]
