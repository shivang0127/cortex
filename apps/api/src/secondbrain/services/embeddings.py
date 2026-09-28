"""Embedding coverage and generation (ARCHITECTURE.md §7).

Only *retrieval* chunks (the ~300-token children) are embedded: they are what
search matches; their parent sections are what a later phase hands to an LLM.
Rows are keyed by (chunk, model), so "what still needs embedding" is a query,
generation is idempotent, and a model change is a re-index rather than a
schema change.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from secondbrain.db.models import Chunk, ChunkEmbedding, Document, Job
from secondbrain.providers.embedding.base import EmbeddingProvider
from secondbrain.queue import queue

EMBED_JOB = "embed.chunks"


class DimensionMismatch(ValueError):
    """The provider's vectors do not fit the chunk_embeddings column."""


def _retrieval_chunks(document_id: uuid.UUID | None = None):
    stmt = select(Chunk).where(Chunk.parent_id.is_not(None), Chunk.superseded_at.is_(None))
    if document_id is not None:
        stmt = stmt.where(Chunk.document_id == document_id)
    return stmt


def _has_embedding(model: str):
    return exists().where(ChunkEmbedding.chunk_id == Chunk.id, ChunkEmbedding.model == model)


def chunks_missing_embeddings(session: Session, document_id: uuid.UUID, model: str) -> list[Chunk]:
    stmt = _retrieval_chunks(document_id).where(~_has_embedding(model)).order_by(Chunk.ordinal)
    return list(session.execute(stmt).scalars())


def embed_document(
    session: Session,
    document_id: uuid.UUID,
    provider: EmbeddingProvider,
    *,
    expected_dimension: int,
    force: bool = False,
    batch_size: int = 64,
) -> int:
    """Embed the document's retrieval chunks that lack a vector for this model.

    Returns the number of vectors written. With `force`, existing vectors for
    the model are dropped first so everything is regenerated.
    """
    if provider.dimension != expected_dimension:
        raise DimensionMismatch(
            f"{provider.model_id} produces {provider.dimension}-d vectors but the "
            f"chunk_embeddings column is {expected_dimension}-d; a migration is required"
        )
    model = provider.model_id
    if force:
        session.execute(
            delete(ChunkEmbedding).where(
                ChunkEmbedding.model == model,
                ChunkEmbedding.chunk_id.in_(
                    select(Chunk.id).where(Chunk.document_id == document_id)
                ),
            )
        )
    chunks = chunks_missing_embeddings(session, document_id, model)
    written = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = provider.embed_documents([c.text for c in batch])
        if len(vectors) != len(batch):
            raise RuntimeError(f"provider returned {len(vectors)} vectors for {len(batch)} texts")
        for chunk, vector in zip(batch, vectors, strict=True):
            if len(vector) != expected_dimension:
                raise DimensionMismatch(
                    f"provider returned a {len(vector)}-d vector; expected {expected_dimension}"
                )
            session.add(ChunkEmbedding(chunk_id=chunk.id, model=model, embedding=vector))
        session.flush()
        written += len(batch)
    return written


def delete_embeddings_for_chunks(session: Session, chunk_ids: list[uuid.UUID]) -> None:
    if chunk_ids:
        session.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))


# ── Coverage / status ────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmbeddingStatus:
    model: str
    dimension: int
    chunks_total: int
    chunks_embedded: int
    documents_total: int
    documents_complete: int
    jobs_queued: int
    jobs_running: int
    jobs_failed: int


def embedded_count_subquery(model: str):
    """Per-document count of live retrieval chunks embedded with `model`."""
    return (
        select(Chunk.document_id, func.count(ChunkEmbedding.id).label("n"))
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
        .where(
            ChunkEmbedding.model == model,
            Chunk.parent_id.is_not(None),
            Chunk.superseded_at.is_(None),
        )
        .group_by(Chunk.document_id)
        .subquery()
    )


def embedded_chunk_count(session: Session, document_id: uuid.UUID, model: str) -> int:
    return session.execute(
        select(func.count(ChunkEmbedding.id))
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .where(
            ChunkEmbedding.model == model,
            Chunk.document_id == document_id,
            Chunk.parent_id.is_not(None),
            Chunk.superseded_at.is_(None),
        )
    ).scalar_one()


def documents_needing_embeddings(session: Session, model: str) -> list[uuid.UUID]:
    """Ready documents with at least one retrieval chunk lacking a vector for `model`."""
    stmt = (
        select(Chunk.document_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.status == "ready",
            Chunk.parent_id.is_not(None),
            Chunk.superseded_at.is_(None),
            ~_has_embedding(model),
        )
        .distinct()
    )
    return list(session.execute(stmt).scalars())


def status(session: Session, model: str, dimension: int) -> EmbeddingStatus:
    live = _retrieval_chunks()
    chunks_total = session.execute(select(func.count()).select_from(live.subquery())).scalar_one()
    chunks_embedded = session.execute(
        select(func.count()).select_from(live.where(_has_embedding(model)).subquery())
    ).scalar_one()
    documents_total = session.execute(
        select(func.count(func.distinct(Chunk.document_id))).select_from(live.subquery())
    ).scalar_one()
    incomplete = len(documents_needing_embeddings(session, model))
    job_counts = dict(
        session.execute(
            select(Job.status, func.count(Job.id)).where(Job.type == EMBED_JOB).group_by(Job.status)
        ).all()
    )
    return EmbeddingStatus(
        model=model,
        dimension=dimension,
        chunks_total=chunks_total,
        chunks_embedded=chunks_embedded,
        documents_total=documents_total,
        documents_complete=documents_total - incomplete,
        jobs_queued=job_counts.get("queued", 0),
        jobs_running=job_counts.get("running", 0),
        jobs_failed=job_counts.get("failed", 0),
    )


def _documents_with_pending_embed_job(session: Session) -> set[uuid.UUID]:
    rows = session.execute(
        select(Job.payload["document_id"].astext).where(
            Job.type == EMBED_JOB, Job.status.in_(("queued", "running"))
        )
    ).scalars()
    return {uuid.UUID(v) for v in rows if v}


def enqueue_index(session: Session, model: str, *, force: bool = False) -> tuple[int, int]:
    """Queue one embed job per document that needs it. Returns (enqueued, skipped).

    Resumable by construction: documents already fully embedded are not queued
    (unless `force`), and documents with a job already waiting are not queued twice.
    """
    if force:
        targets = list(
            session.execute(select(Document.id).where(Document.status == "ready")).scalars()
        )
    else:
        targets = documents_needing_embeddings(session, model)
    pending = _documents_with_pending_embed_job(session)
    enqueued = 0
    for document_id in targets:
        if document_id in pending:
            continue
        queue.enqueue(session, EMBED_JOB, {"document_id": str(document_id), "force": force})
        enqueued += 1
    return enqueued, len(targets) - enqueued
