"""Unit of Work transaction boundary."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from .repositories import (
    AgentTaskRepository,
    ArtifactRepository,
    CampaignRepository,
    ContextSnapshotRepository,
    EventLogRepository,
    OperationIntelligenceRepository,
    OperationRepository,
    SchemaRepository,
    TestObservationRepository,
)
from .session import SessionLocal


class UnitOfWork:
    """Context-managed transaction with all MVP repositories."""

    def __init__(self, session_factory: sessionmaker[Session] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        self.session = self.session_factory()
        self.schemas = SchemaRepository(self.session)
        self.operations = OperationRepository(self.session)
        self.intelligence = OperationIntelligenceRepository(self.session)
        self.tasks = AgentTaskRepository(self.session)
        self.campaigns = CampaignRepository(self.session)
        self.artifacts = ArtifactRepository(self.session)
        self.observations = TestObservationRepository(self.session)
        self.context_snapshots = ContextSnapshotRepository(self.session)
        self.events = EventLogRepository(self.session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del tb
        if self.session is None:
            return
        if exc_type is not None:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.rollback()
