"""SQLAlchemy implementation of the catalog transaction port."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from .repositories import (
    SqlAlchemyGeneratorConfigRepository,
    SqlAlchemyResourceCatalogRepository,
    SqlAlchemyResponseValueCatalogRepository,
    SqlAlchemySchemaRepository,
    SqlAlchemySmokeMemoryRepository,
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
        """
        Commit the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """
        Roll back the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Commit the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """
        Roll back the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Commit the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """
        Roll back the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Commit the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """
        Roll back the current transaction for the repository and database persistence
        boundary.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.rollback()


class SqlAlchemySmokeMemoryUnitOfWork:
    """Own one atomic transaction for Operation Smoke knowledge.

    Failure Dedup uses this boundary to record validated Failure groups.
    Failure Solve also uses it to change Generator state and write the matching
    Investigation in the same database transaction. Exposing both repositories
    on one session prevents a configuration from changing without an
    explanation, or an Applied Patch record from existing without its change.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Remember how to create sessions; opening is delayed until ``with``."""
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "SqlAlchemySmokeMemoryUnitOfWork":
        """Open a transaction and construct repositories over that transaction."""
        self.session = self.session_factory()
        self.smoke_memory = SqlAlchemySmokeMemoryRepository(self.session)
        self.generator_configs = SqlAlchemyGeneratorConfigRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back unfinished work and release the owned database session."""
        del exc, tb
        if self.session is None:
            return
        # A caller must explicitly commit a complete domain operation.  An
        # exception or forgotten commit therefore cannot leave partial state.
        if exc_type is not None or self.session.in_transaction():
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        """Atomically persist every Generator and Smoke-memory write."""
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """Discard every pending write in the active atomic operation."""
        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.rollback()
