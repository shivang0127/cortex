from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SearchModeIn = Literal["hybrid", "semantic", "keyword"]


class SearchHitOut(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    document_kind: str
    origin_uri: str | None = None
    subjects: list[str]
    week: int | None = None
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    ordinal: int
    text: str
    score: float = Field(
        description="Ranking score: RRF for hybrid, cosine for semantic, ts_rank for keyword"
    )
    similarity: float | None = Field(
        default=None, description="Cosine similarity (semantic/hybrid)"
    )
    keyword_score: float | None = Field(default=None, description="ts_rank_cd (keyword/hybrid)")
    semantic_rank: int | None = None
    keyword_rank: int | None = None


class SearchResponse(BaseModel):
    query: str
    mode: SearchModeIn
    model: str | None = Field(
        default=None, description="Embedding model used for the semantic side"
    )
    semantic_candidates: int
    keyword_candidates: int
    results: list[SearchHitOut]


class EmbeddingStatusOut(BaseModel):
    enabled: bool
    provider: str | None = None
    model: str | None = None
    dimension: int
    chunks_total: int
    chunks_embedded: int
    documents_total: int
    documents_complete: int
    jobs_queued: int
    jobs_running: int
    jobs_failed: int


class IndexRequest(BaseModel):
    force: bool = Field(default=False, description="Regenerate even where vectors exist")


class IndexResponse(BaseModel):
    enqueued: int
    skipped: int = Field(description="Documents that already had a job waiting")
    model: str
