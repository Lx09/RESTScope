"""Engine and session helpers owned by the SQLAlchemy adapter."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from restscope.config import DBConfig


def create_engine_from_url(url: str, *, echo: bool = False, **kwargs) -> Engine:
    """Create a synchronous engine and enforce SQLite foreign keys.

    SQLite accepts foreign-key declarations while leaving their enforcement
    disabled on every new connection.  Registering the pragma on the Engine is
    therefore part of the database integrity contract, not an optional tuning
    choice.
    """

    engine = create_engine(url, echo=echo, future=True, **kwargs)
    enable_sqlite_foreign_keys(engine)
    return engine


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable foreign-key checks for every SQLite connection from ``engine``.

    Non-SQLite engines already enforce their declared constraints and are left
    untouched.  The DB-API cursor is always closed, including when the pragma
    itself fails during connection setup.
    """

    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


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
