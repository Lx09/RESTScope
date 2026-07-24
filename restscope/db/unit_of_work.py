"""SQLAlchemy implementation of the catalog transaction port."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from .repositories import (
    SqlAlchemyGeneratorConfigRepository,
    SqlAlchemyResourceCatalogRepository,
    SqlAlchemyResponseValueCatalogRepository,
    SqlAlchemySchemaRepository,
)


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


class SqlAlchemyGeneratorConfigUnitOfWork:
    """Context-managed transaction for generator configuration persistence."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyGeneratorConfigUnitOfWork":
        self.session = self.session_factory()
        self.generator_configs = SqlAlchemyGeneratorConfigRepository(self.session)
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


class SqlAlchemyResourceCatalogUnitOfWork:
    """Context-managed transaction for resource catalog persistence."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyResourceCatalogUnitOfWork":
        self.session = self.session_factory()
        self.resources = SqlAlchemyResourceCatalogRepository(self.session)
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


class SqlAlchemyResponseValueCatalogUnitOfWork:
    """Context-managed transaction for generic response-value evidence."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemyResponseValueCatalogUnitOfWork":
        self.session = self.session_factory()
        self.response_values = SqlAlchemyResponseValueCatalogRepository(self.session)
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
