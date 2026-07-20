"""Engine and session helpers owned by the SQLAlchemy adapter."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from restscope.restscope_config import DBConfig


def create_engine_from_url(url: str, *, echo: bool = False, **kwargs) -> Engine:
    """Create a synchronous SQLAlchemy engine."""

    return create_engine(url, echo=echo, future=True, **kwargs)


def create_engine_from_config(config: DBConfig) -> Engine:
    """Create an engine from explicit database configuration."""

    kwargs: dict[str, object] = {}
    if not config.url.startswith("sqlite"):
        if config.pool_size is not None:
            kwargs["pool_size"] = config.pool_size
        if config.max_overflow is not None:
            kwargs["max_overflow"] = config.max_overflow
    return create_engine_from_url(config.url, echo=config.echo, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a sessionmaker bound to the given engine."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
