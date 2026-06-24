from __future__ import annotations

from sqlalchemy import select

from ..orm import ArtifactORM
from ..records import ArtifactRecord
from .base_repo import BaseRepository


class ArtifactRepository(BaseRepository[ArtifactORM, ArtifactRecord]):
    orm_class = ArtifactORM
    record_class = ArtifactRecord

    def list_by_task(self, task_id: str) -> list[ArtifactRecord]:
        return self.to_records(
            self.session.scalars(select(ArtifactORM).where(ArtifactORM.task_id == task_id)).all()
        )

    def list_by_campaign(self, campaign_id: str) -> list[ArtifactRecord]:
        return self.to_records(
            self.session.scalars(select(ArtifactORM).where(ArtifactORM.campaign_id == campaign_id)).all()
        )

    def list_by_observation(self, observation_id: str) -> list[ArtifactRecord]:
        return self.to_records(
            self.session.scalars(select(ArtifactORM).where(ArtifactORM.observation_id == observation_id)).all()
        )
