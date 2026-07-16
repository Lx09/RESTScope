"""Repository interfaces for RESTScope DB tables."""

from .artifact_repo import ArtifactRepository
from .campaign_repo import CampaignRepository
from .context_snapshot_repo import ContextSnapshotRepository
from .event_log_repo import EventLogRepository
from .intelligence_repo import OperationIntelligenceRepository
from .observation_repo import TestObservationRepository
from .operation_repo import OperationRepository
from .operation_edge_repo import OperationEdgeRepository
from .schema_repo import SchemaRepository
from .task_repo import AgentTaskRepository

__all__ = [
    "ArtifactRepository",
    "CampaignRepository",
    "ContextSnapshotRepository",
    "EventLogRepository",
    "OperationIntelligenceRepository",
    "TestObservationRepository",
    "OperationRepository",
    "OperationEdgeRepository",
    "SchemaRepository",
    "AgentTaskRepository",
]
