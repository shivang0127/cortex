"""L4: chunk_embeddings (pgvector) with an HNSW cosine index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# bge-small-en-v1.5 produces 384-d vectors. pgvector indexes are fixed-width, so a
# model with a different width needs a new column or table in a later migration.
DIMENSION = 384


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("embedding", Vector(DIMENSION), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("chunk_id", "model", name="uq_chunk_embeddings_chunk_model"),
    )
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])
    op.create_index("ix_chunk_embeddings_model", "chunk_embeddings", ["model"])
    # HNSW rather than IVFFlat: no training step, handles the one-document-at-a-time
    # insert pattern, and recall stays good as the corpus grows (ARCHITECTURE.md §7).
    op.create_index(
        "ix_chunk_embeddings_hnsw",
        "chunk_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("chunk_embeddings")
