"""Database-layer ID helpers."""

from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """Create a compact prefixed text ID."""

    return f"{prefix}_{uuid4().hex}"


def new_schema_id() -> str:
    return new_id("schema")


def new_operation_id() -> str:
    return new_id("op")


def new_operation_edge_id() -> str:
    return new_id("edge")


def new_task_id() -> str:
    return new_id("task")


def new_campaign_id() -> str:
    return new_id("camp")


def new_artifact_id() -> str:
    return new_id("artifact")


def new_observation_id() -> str:
    return new_id("obs")


def new_snapshot_id() -> str:
    return new_id("ctx")
