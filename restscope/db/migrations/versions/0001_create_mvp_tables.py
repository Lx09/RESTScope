"""create MVP DB tables

Revision ID: 0001_create_mvp_tables
Revises:
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_create_mvp_tables"
down_revision = None
branch_labels = None
depends_on = None


def _json_type():
    return postgresql.JSONB() if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def _text_array_type():
    return postgresql.ARRAY(sa.Text()) if op.get_bind().dialect.name == "postgresql" else sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    text_array = _text_array_type()

    op.create_table(
        "schemas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.String()),
        sa.Column("spec_hash", sa.String(), nullable=False),
        sa.Column("raw_spec_uri", sa.String(), nullable=False),
        sa.Column("normalized_spec_uri", sa.String()),
        sa.Column("openapi_version", sa.String()),
        sa.Column("operation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_schemas_hash", "schemas", ["spec_hash"], unique=True)

    op.create_table(
        "operations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("operation_id", sa.String()),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("tags", text_array, nullable=False),
        sa.Column("summary", sa.String()),
        sa.Column("resource", sa.String()),
        sa.Column("mutability", sa.String()),
        sa.Column("security", json_type),
        sa.Column("request_schema_refs", text_array, nullable=False),
        sa.Column("response_schema_refs", text_array, nullable=False),
        sa.Column("card_json", json_type, nullable=False),
        sa.Column("static_risk_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_operations_schema", "operations", ["schema_id"])
    op.create_index("idx_operations_method_path", "operations", ["schema_id", "method", "path"])
    op.create_index("idx_operations_static_risk", "operations", ["schema_id", "static_risk_score"])

    op.create_table(
        "operation_intelligence",
        sa.Column("operation_id", sa.String(), sa.ForeignKey("operations.id"), primary_key=True),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("test_state", sa.String(), nullable=False, server_default="profiled"),
        sa.Column("dynamic_risk_score", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("failure_density", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("flake_rate", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True)),
        sa.Column("total_campaigns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cases_executed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed_issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("server_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contract_violation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("semantic_violation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flake_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("learned_constraint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_confidence_constraint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recommended_checks", text_array, nullable=False),
        sa.Column("regression_priority", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("summary_json", json_type, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_operation_intelligence_schema_risk", "operation_intelligence", ["schema_id", "dynamic_risk_score"])
    op.create_index("idx_operation_intelligence_regression", "operation_intelligence", ["schema_id", "regression_priority"])
    op.create_index("idx_operation_intelligence_state", "operation_intelligence", ["schema_id", "test_state"])

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("target_env_id", sa.String()),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("goal_json", json_type, nullable=False),
        sa.Column("budget_json", json_type, nullable=False),
        sa.Column("cycle_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_campaign_id", sa.String()),
        sa.Column("selected_operation_ids", text_array, nullable=False),
        sa.Column("current_hypotheses", text_array, nullable=False),
        sa.Column("current_check_ids", text_array, nullable=False),
        sa.Column("context_snapshot_id", sa.String()),
        sa.Column("latest_report_uri", sa.String()),
        sa.Column("blockers_json", json_type, nullable=False),
        sa.Column("last_error", sa.String()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_agent_tasks_schema", "agent_tasks", ["schema_id"])
    op.create_index("idx_agent_tasks_state", "agent_tasks", ["state"])

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("agent_tasks.id"), nullable=False),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("target_env_id", sa.String()),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("campaign_type", sa.String(), nullable=False),
        sa.Column("campaign_spec_json", json_type, nullable=False),
        sa.Column("validation_result_json", json_type),
        sa.Column("summary_json", json_type),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("artifact_bundle_uri", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_campaigns_task", "campaigns", ["task_id"])
    op.create_index("idx_campaigns_schema_status", "campaigns", ["schema_id", "status"])
    op.create_index("idx_campaigns_type", "campaigns", ["schema_id", "campaign_type"])

    op.create_table(
        "test_observations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("agent_tasks.id"), nullable=False),
        sa.Column("campaign_id", sa.String(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("operations.id")),
        sa.Column("observation_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="observed"),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("confidence", sa.Numeric(), nullable=False, server_default="0.5"),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column("check_id", sa.String()),
        sa.Column("request_fingerprint", sa.String()),
        sa.Column("response_fingerprint", sa.String()),
        sa.Column("request_summary_json", json_type),
        sa.Column("response_summary_json", json_type),
        sa.Column("reproducer_artifact_id", sa.String()),
        sa.Column("raw_artifact_id", sa.String()),
        sa.Column("hypothesis", sa.String()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("idx_test_observations_dedupe", "test_observations", ["schema_id", "dedupe_key"], unique=True)
    op.create_index("idx_test_observations_operation", "test_observations", ["schema_id", "operation_id"])
    op.create_index("idx_test_observations_type", "test_observations", ["schema_id", "observation_type"])
    op.create_index("idx_test_observations_status", "test_observations", ["schema_id", "status"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String()),
        sa.Column("campaign_id", sa.String()),
        sa.Column("observation_id", sa.String()),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("artifact_uri", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("metadata_json", json_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_artifacts_task", "artifacts", ["task_id"])
    op.create_index("idx_artifacts_campaign", "artifacts", ["campaign_id"])
    op.create_index("idx_artifacts_observation", "artifacts", ["observation_id"])
    op.create_index("idx_artifacts_type", "artifacts", ["artifact_type"])

    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("agent_tasks.id"), nullable=False),
        sa.Column("schema_id", sa.String(), sa.ForeignKey("schemas.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("cycle_index", sa.Integer(), nullable=False),
        sa.Column("artifact_uri", sa.String(), nullable=False),
        sa.Column("source_refs_json", json_type),
        sa.Column("total_estimated_tokens", sa.Integer()),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_context_snapshots_task", "context_snapshots", ["task_id", "cycle_index"])
    op.create_index("idx_context_snapshots_role", "context_snapshots", ["schema_id", "role"])

    op.create_table(
        "event_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String()),
        sa.Column("campaign_id", sa.String()),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("from_state", sa.String()),
        sa.Column("to_state", sa.String()),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_event_log_task", "event_log", ["task_id", "created_at"])
    op.create_index("idx_event_log_campaign", "event_log", ["campaign_id", "created_at"])
    op.create_index("idx_event_log_type", "event_log", ["event_type", "created_at"])


def downgrade() -> None:
    for table in [
        "event_log",
        "context_snapshots",
        "artifacts",
        "test_observations",
        "campaigns",
        "agent_tasks",
        "operation_intelligence",
        "operations",
        "schemas",
    ]:
        op.drop_table(table)
