"""foundation: extensions + jobs table

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions the whole architecture depends on (ARCHITECTURE.md §1, §7).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # pgvector: embeddings, HNSW
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # trigram similarity: alias lookup

    # The job queue — the only application table that exists before ingestion (Phase 1).
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('queued','running','succeeded','failed','cancelled')",
            name="status",  # naming convention → ck_jobs_status
        ),
    )
    op.create_index(
        "ix_jobs_queued",
        "jobs",
        ["status", "priority", "run_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_queued", table_name="jobs")
    op.drop_table("jobs")
    # Extensions are left installed: dropping them would destroy any other data
    # that depends on them, and they are harmless when unused.
