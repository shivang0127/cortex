"""L1 — source layer (ARCHITECTURE.md §3) plus the subject organisation tables.

`documents` and `chunks` are the immutable ground truth every later layer points
into. Chunks index into `documents.raw_text` by character offset, so any piece
of derived knowledge can be traced back to the exact span that produced it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from secondbrain.db.base import Base

DOCUMENT_KINDS = ("pdf", "markdown", "text", "docx", "web", "youtube")
DOCUMENT_STATUSES = ("pending", "processing", "ready", "failed")
CLASSIFIED_BY = ("none", "user", "ai", "filename")

# Many-to-many: a document can be filed under zero, one or many subjects.
document_subjects = Table(
    "document_subjects",
    Base.metadata,
    Column(
        "document_id",
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "subject_id",
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class Subject(Base):
    """A university subject such as "Theory of Computing Science". Metadata, not a concept."""

    __tablename__ = "subjects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    documents: Mapped[list["Document"]] = relationship(
        secondary=document_subjects, back_populates="subjects"
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind in ('pdf','markdown','text','docx','web','youtube')", name="kind"),
        CheckConstraint("status in ('pending','processing','ready','failed')", name="status"),
        CheckConstraint("classified_by in ('none','user','ai','filename')", name="classified_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    origin_uri: Mapped[str | None] = mapped_column(Text)  # informational only
    storage_path: Mapped[str | None] = mapped_column(Text)  # relative to DATA_DIR; null for URLs
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)  # normalised full text; chunks index into it
    # Organisation (§0): metadata, never graph nodes. Subjects via document_subjects.
    week: Mapped[int | None] = mapped_column(Integer, index=True)
    classified_by: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    classification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    subjects: Mapped[list[Subject]] = relationship(
        secondary=document_subjects, back_populates="documents", order_by=Subject.name
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Chunk.ordinal"
    )

    def __repr__(self) -> str:
        return f"<Document {self.id} {self.kind} {self.status} {self.title!r}>"


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        # Ordinals are unique among live chunks; superseded chunks keep theirs so
        # evidence recorded against them never dangles (ARCHITECTURE.md §3).
        Index(
            "uq_chunks_document_ordinal_live",
            "document_id",
            "ordinal",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        CheckConstraint("char_start >= 0 and char_end > char_start", name="span"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)  # offsets into raw_text
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), index=True
    )  # null ⇒ section-level chunk; set ⇒ retrieval-sized child of that section
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))

    document: Mapped[Document] = relationship(back_populates="chunks")
    parent: Mapped["Chunk | None"] = relationship(remote_side=[id])

    def __repr__(self) -> str:
        return f"<Chunk {self.ordinal} of {self.document_id} [{self.char_start}:{self.char_end}]>"
