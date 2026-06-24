"""Campaign memory from campaigns and artifacts."""

from __future__ import annotations

from restscope.db.records import CampaignRecord
from restscope.db.repositories import ArtifactRepository, CampaignRepository

from ..schemas import MemoryItem


class CampaignMemoryStore:
    def __init__(
        self,
        campaign_repo: CampaignRepository,
        artifact_repo: ArtifactRepository,
    ) -> None:
        self.campaign_repo = campaign_repo
        self.artifact_repo = artifact_repo

    def list_recent_campaigns(self, task_id: str, limit: int) -> list[MemoryItem]:
        return [self._item(record) for record in self.campaign_repo.list_recent_by_task(task_id, limit=limit)]

    def summarize_campaign_history(self, task_id: str) -> MemoryItem:
        campaigns = self.campaign_repo.list_recent_by_task(task_id, limit=20)
        completed = sum(1 for campaign in campaigns if campaign.status == "completed")
        failed = sum(1 for campaign in campaigns if campaign.status == "failed")
        content = f"{len(campaigns)} recent campaigns: {completed} completed, {failed} failed."
        return MemoryItem(
            id=f"mem_campaign_summary_{task_id}",
            kind="campaign",
            task_id=task_id,
            title="Campaign history summary",
            content=content,
            structured={
                "campaign_count": len(campaigns),
                "completed_count": completed,
                "failed_count": failed,
                "campaign_ids": [campaign.id for campaign in campaigns],
            },
            importance=0.6,
            confidence=0.8,
            source_table="campaigns",
            source_id=f"summary:{task_id}",
        )

    def _item(self, campaign: CampaignRecord) -> MemoryItem:
        artifacts = self.artifact_repo.list_by_campaign(campaign.id)
        content = (
            f"{campaign.campaign_type} campaign is {campaign.status}. "
            f"Summary: {campaign.summary_json or {}}."
        )
        return MemoryItem(
            id=f"mem_campaign_{campaign.id}",
            kind="campaign",
            schema_id=campaign.schema_id,
            task_id=campaign.task_id,
            campaign_id=campaign.id,
            title=f"{campaign.campaign_type} campaign {campaign.status}",
            content=content,
            structured={
                "campaign_type": campaign.campaign_type,
                "status": campaign.status,
                "summary": campaign.summary_json or {},
                "artifact_bundle_uri": campaign.artifact_bundle_uri,
                "artifact_ids": [artifact.id for artifact in artifacts],
            },
            importance=0.7 if campaign.status in {"completed", "failed"} else 0.5,
            confidence=0.8,
            recency_score=0.8,
            source_table="campaigns",
            source_id=campaign.id,
        )
