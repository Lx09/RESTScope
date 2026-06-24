"""Engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from restscope.restscope_config import CONFIG, DBConfig


def create_engine_from_url(url: str, *, echo: bool = False, **kwargs) -> Engine:
    """Create a synchronous SQLAlchemy engine."""

    return create_engine(url, echo=echo, future=True, **kwargs)


def create_engine_from_config(config: DBConfig | None = None) -> Engine:
    """Create an engine from RESTScope DB config."""

    db = config or CONFIG.db
    kwargs: dict[str, object] = {}
    if not db.url.startswith("sqlite"):
        if db.pool_size is not None:
            kwargs["pool_size"] = db.pool_size
        if db.max_overflow is not None:
            kwargs["max_overflow"] = db.max_overflow
    return create_engine_from_url(db.url, echo=db.echo, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a sessionmaker bound to the given engine."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


EngineLocal = create_engine_from_config()
SessionLocal = make_session_factory(EngineLocal)


@contextmanager
def session_scope(factory: sessionmaker[Session] = SessionLocal) -> Iterator[Session]:
    """Provide a transactional session scope."""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
