"""Create approval workflow, audit, outbox and idempotency tables.

Revision ID: 20260714_0001
Revises: None
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260714_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=255), nullable=False),
        sa.Column("decision_kind", sa.String(length=20), nullable=True),
        sa.Column("decision_text", sa.Text(), nullable=True),
        sa.Column("finalized_by_user_id", sa.String(length=255), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision_kind IS NULL OR decision_kind IN ('approved', 'rejected', 'cancelled')",
            name="ck_approval_requests_decision_kind",
        ),
        sa.CheckConstraint(
            "source_type IN ('publication', 'scenario', 'edit', 'external')",
            name="ck_approval_requests_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approval_requests_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_approval_requests_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_requests_workspace_created",
        "approval_requests",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_approval_requests_workspace_status",
        "approval_requests",
        ["workspace_id", "status"],
    )

    op.create_table(
        "approval_reviewers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "user_id", name="uq_approval_reviewer_request_user"
        ),
    )
    op.create_index(
        "ix_approval_reviewers_request_id", "approval_reviewers", ["request_id"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_events_workspace_request",
        "audit_events",
        ["workspace_id", "request_id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts"),
        sa.CheckConstraint("schema_version >= 1", name="ck_outbox_events_schema_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_events_unpublished", "outbox_events", ["published_at", "created_at"]
    )
    op.create_index("ix_outbox_events_workspace", "outbox_events", ["workspace_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=255), nullable=False),
        sa.Column("actor_user_id", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", sa.JSON(), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_idempotency_workspace_key"
        ),
    )
    op.create_index(
        "ix_idempotency_records_created", "idempotency_records", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_records_created", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_outbox_events_workspace", table_name="outbox_events")
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_audit_events_workspace_request", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_approval_reviewers_request_id", table_name="approval_reviewers")
    op.drop_table("approval_reviewers")
    op.drop_index("ix_approval_requests_workspace_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_workspace_created", table_name="approval_requests")
    op.drop_table("approval_requests")
