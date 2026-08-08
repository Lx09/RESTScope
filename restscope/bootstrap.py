"""Composition roots that wire domain services to infrastructure adapters."""

from __future__ import annotations

from restscope.catalog import OpenAPICatalog
from restscope.db import (
    SqlAlchemyGeneratorConfigUnitOfWork,
    SqlAlchemyOpenAPIUnitOfWork,
    create_engine_from_config,
    make_session_factory,
)
from restscope.restscope_config import RESTScopeConfig
from restscope.harness.testing import GeneratorConfigCatalog


def build_openapi_catalog(config: RESTScopeConfig) -> OpenAPICatalog:
    """Build the normalized OpenAPI audit catalog for one App database."""

    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    return OpenAPICatalog(lambda: SqlAlchemyOpenAPIUnitOfWork(session_factory))


def build_generator_config_catalog(config: RESTScopeConfig) -> GeneratorConfigCatalog:
    """Build the single-API generator catalog from the configured database."""

    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    return GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(session_factory)
    )
