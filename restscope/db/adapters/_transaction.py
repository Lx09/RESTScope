"""Share the mechanical SQLAlchemy transaction lifecycle across domain adapters.

Concrete domain adapter modules open repositories on the session returned by
``_open_session``. This private base owns only commit, rollback, and guaranteed
session cleanup; it does not choose repositories or expose a multi-domain
service locator.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker


class _SqlAlchemyUnitOfWork:
    """Own one lazily opened SQLAlchemy session for a domain transaction."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Remember the factory without opening a connection before ``with``."""

        self.session_factory = session_factory
        self.session: Session | None = None

    def _open_session(self) -> Session:
        """Open and retain the session used by a concrete adapter's repositories."""

        if self.session is not None:
            raise RuntimeError("Unit of work is already active")
        self.session = self.session_factory()
        return self.session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back unfinished work and always close the active session."""

        del exc, tb
        if self.session is None:
            return
        if exc_type is not None or self.session.in_transaction():
            self.session.rollback()
        self.session.close()
        self.session = None

    def commit(self) -> None:
        """Commit the complete domain operation in the active transaction."""

        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.commit()

    def rollback(self) -> None:
        """Discard every pending write in the active transaction."""

        if self.session is None:
            raise RuntimeError("Unit of work is not active")
        self.session.rollback()
