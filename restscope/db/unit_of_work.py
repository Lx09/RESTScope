"""SQLAlchemy implementation of the catalog transaction port."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from .repositories import SqlAlchemySchemaRepository


class SqlAlchemySchemaUnitOfWork:
    """Context-managed transaction for schema source persistence."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemySchemaUnitOfWork":
        self.session = self.session_factory()
        self.schemas = SqlAlchemySchemaRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc, tb
        if self.session is None:
            return
        if exc_type is not None or self.session.in_transaction():
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.rollback()
