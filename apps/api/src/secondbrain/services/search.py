"""Chunk retrieval: semantic, keyword, and hybrid (ARCHITECTURE.md §7).

Three modes over the same population — live retrieval chunks of ready documents:

* **semantic** — pgvector cosine distance between the query embedding and
  `chunk_embeddings` for the configured model (HNSW index).
* **keyword** — PostgreSQL full-text search over `chunks.tsv` with
  `websearch_to_tsquery`, ranked by `ts_rank_cd`.
* **hybrid** — both, fused with Reciprocal Rank Fusion: each hit scores
  Σ 1 / (k + rank) across the lists it appears in. Ranks, not scores, so the
  two signals need no calibration against each other; a chunk that both agree
  on rises to the top, and a rare exact term the embedding missed still
  surfaces.

This is retrieval only. Nothing here generates text; that is Phase 3.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from secondbrain.db.models import Chunk, ChunkEmbedding, Document, document_subjects
from secondbrain.providers.embedding.base import EmbeddingProvider

SearchMode = Literal["hybrid", "semantic", "keyword"]


@dataclass(frozen=True)
class SearchFilters:
    subject_id: uuid.UUID | None = None
    week: int | None = None
    kind: str | None = None
    document_id: uuid.UUID | None = None


@dataclass
class SearchHit:
    chunk: Chunk
    document: Document
    score: float
    semantic_rank: int | None = None
    keyword_rank: int | None = None
    similarity: float | None = None  # cosine similarity, semantic only
    keyword_score: float | None = None  # ts_rank_cd, keyword only


@dataclass
class SearchResult:
    query: str
    mode: SearchMode
    hits: list[SearchHit] = field(default_factory=list)
    semantic_candidates: int = 0
    keyword_candidates: int = 0


def _base(filters: SearchFilters) -> Select:
    """Live retrieval chunks of ready documents, joined to their document."""
    stmt = (
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.parent_id.is_not(None),
            Chunk.superseded_at.is_(None),
            Document.status == "ready",
        )
    )
    if filters.subject_id is not None:
        stmt = stmt.where(
            Document.id.in_(
                select(document_subjects.c.document_id).where(
                    document_subjects.c.subject_id == filters.subject_id
                )
            )
        )
    if filters.week is not None:
        stmt = stmt.where(Document.week == filters.week)
    if filters.kind is not None:
        stmt = stmt.where(Document.kind == filters.kind)
    if filters.document_id is not None:
        stmt = stmt.where(Document.id == filters.document_id)
    return stmt.options(selectinload(Document.subjects))


def semantic_candidates(
    session: Session,
    query_vector: list[float],
    model: str,
    filters: SearchFilters,
    limit: int,
) -> list[tuple[Chunk, Document, float]]:
    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
    stmt = (
        _base(filters)
        .add_columns(distance.label("distance"))
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
        .where(ChunkEmbedding.model == model)
        .order_by(distance)
        .limit(limit)
    )
    return [(chunk, doc, 1.0 - float(dist)) for chunk, doc, dist in session.execute(stmt).all()]


def keyword_candidates(
    session: Session, query: str, filters: SearchFilters, limit: int
) -> list[tuple[Chunk, Document, float]]:
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery)
    stmt = (
        _base(filters)
        .add_columns(rank.label("rank"))
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(rank.desc(), Chunk.ordinal)
        .limit(limit)
    )
    return [(chunk, doc, float(r)) for chunk, doc, r in session.execute(stmt).all()]


def reciprocal_rank_fusion(
    ranked_lists: list[list[uuid.UUID]], k: int = 60
) -> dict[uuid.UUID, float]:
    """RRF: score(id) = Σ over lists containing id of 1 / (k + rank), rank starting at 1."""
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def search(
    session: Session,
    query: str,
    *,
    mode: SearchMode,
    provider: EmbeddingProvider | None,
    limit: int,
    filters: SearchFilters | None = None,
    candidates: int = 50,
    rrf_k: int = 60,
) -> SearchResult:
    query = " ".join(query.split())
    if not query:
        raise ValueError("query must not be blank")
    filters = filters or SearchFilters()
    result = SearchResult(query=query, mode=mode)
    depth = max(candidates, limit)

    semantic: list[tuple[Chunk, Document, float]] = []
    keyword: list[tuple[Chunk, Document, float]] = []
    if mode in ("semantic", "hybrid"):
        if provider is None:
            raise ValueError("semantic search needs an embedding provider")
        vector = provider.embed_query(query)
        semantic = semantic_candidates(session, vector, provider.model_id, filters, depth)
    if mode in ("keyword", "hybrid"):
        keyword = keyword_candidates(session, query, filters, depth)
    result.semantic_candidates = len(semantic)
    result.keyword_candidates = len(keyword)

    by_id: dict[uuid.UUID, SearchHit] = {}
    for rank, (chunk, doc, similarity) in enumerate(semantic, start=1):
        by_id[chunk.id] = SearchHit(
            chunk, doc, score=similarity, semantic_rank=rank, similarity=similarity
        )
    for rank, (chunk, doc, ts_rank) in enumerate(keyword, start=1):
        hit = by_id.get(chunk.id)
        if hit is None:
            hit = by_id[chunk.id] = SearchHit(chunk, doc, score=ts_rank)
        hit.keyword_rank = rank
        hit.keyword_score = ts_rank

    if mode == "hybrid":
        fused = reciprocal_rank_fusion(
            [[c.id for c, _, _ in semantic], [c.id for c, _, _ in keyword]], k=rrf_k
        )
        for chunk_id, score in fused.items():
            by_id[chunk_id].score = score

    ordered = sorted(by_id.values(), key=lambda h: (-h.score, h.chunk.ordinal))
    result.hits = ordered[:limit]
    return result
