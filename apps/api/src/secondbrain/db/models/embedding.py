"""L4 — derived layer: chunk embeddings (ARCHITECTURE.md §3, §7).

Vectors live in their own table, keyed by chunk *and* model, so a second model
can be back-filled beside the first and cut over when complete. The column
width is fixed by the migration; `Settings.embedding_dimension` must match it.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from secondbrain.db.base import Base
from secondbrain.db.models.document import Chunk

EMBEDDING_DIMENSION = 384  # bge-small-en-v1.5; changing this is migration 0004+


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model", name="uq_chunk_embeddings_chunk_model"),
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunk: Mapped[Chunk] = relationship()

    def __repr__(self) -> str:
        return f"<ChunkEmbedding chunk={self.chunk_id} model={self.model}>"
