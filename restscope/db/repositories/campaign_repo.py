from __future__ import annotations

from sqlalchemy import select

from ..orm import CampaignORM
from ..records import CampaignRecord
from .base_repo import BaseRepository


class CampaignRepository(BaseRepository[CampaignORM, CampaignRecord]):
    orm_class = CampaignORM
    record_class = CampaignRecord

    def list_by_task(self, task_id: str) -> list[CampaignRecord]:
        return self.to_records(
            self.session.scalars(select(CampaignORM).where(CampaignORM.task_id == task_id)).all()
        )

    def list_recent_by_task(self, task_id: str, *, limit: int = 10) -> list[CampaignRecord]:
        return self.to_records(
            self.session.scalars(
                select(CampaignORM)
                .where(CampaignORM.task_id == task_id)
                .order_by(CampaignORM.created_at.desc())
                .limit(limit)
            ).all()
        )

    def list_by_schema_status(self, schema_id: str, status: str) -> list[CampaignRecord]:
        return self.to_records(
            self.session.scalars(
                select(CampaignORM).where(CampaignORM.schema_id == schema_id, CampaignORM.status == status)
            ).all()
        )
