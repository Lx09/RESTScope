"""Composition roots that wire domain services to infrastructure adapters."""

from __future__ import annotations

from restscope.catalog import SchemaCatalog
from restscope.db import SqlAlchemySchemaUnitOfWork, create_engine_from_config, make_session_factory
from restscope.restscope_config import RESTScopeConfig


def build_schema_catalog(config: RESTScopeConfig) -> SchemaCatalog:
    """Build a schema catalog backed by the configured SQL database."""

    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    return SchemaCatalog(lambda: SqlAlchemySchemaUnitOfWork(session_factory))
