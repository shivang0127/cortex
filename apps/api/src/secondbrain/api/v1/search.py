import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from secondbrain.config import Settings, get_settings
from secondbrain.db.engine import get_session
from secondbrain.providers.embedding import EmbeddingsDisabled, get_embedding_provider
from secondbrain.providers.embedding.base import EmbeddingError
from secondbrain.schemas.search import SearchHitOut, SearchModeIn, SearchResponse
from secondbrain.services import search as svc
from secondbrain.services.search import SearchFilters, SearchHit

router = APIRouter(prefix="/search", tags=["search"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _hit_out(hit: SearchHit) -> SearchHitOut:
    c, d = hit.chunk, hit.document
    return SearchHitOut(
        chunk_id=c.id,
        document_id=d.id,
        document_title=d.title,
        document_kind=d.kind,
        origin_uri=d.origin_uri,
        subjects=[s.name for s in d.subjects],
        week=d.week,
        heading_path=c.heading_path,
        page_start=c.page_start,
        page_end=c.page_end,
        char_start=c.char_start,
        char_end=c.char_end,
        ordinal=c.ordinal,
        text=c.text,
        score=hit.score,
        similarity=hit.similarity,
        keyword_score=hit.keyword_score,
        semantic_rank=hit.semantic_rank,
        keyword_rank=hit.keyword_rank,
    )


@router.get(
    "",
    response_model=SearchResponse,
    summary="Search knowledge: the contents of chunks, by meaning and/or keywords",
)
def search_chunks(
    session: SessionDep,
    settings: SettingsDep,
    q: Annotated[str, Query(min_length=1, max_length=1000, description="Natural-language query")],
    mode: Annotated[SearchModeIn, Query()] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    subject_id: uuid.UUID | None = None,
    week: Annotated[int | None, Query(ge=0)] = None,
    kind: Annotated[
        Literal["pdf", "markdown", "text", "docx", "web", "youtube"] | None, Query()
    ] = None,
    document_id: uuid.UUID | None = None,
) -> SearchResponse:
    if not q.strip():
        raise HTTPException(422, "query must not be blank")
    provider = None
    if mode != "keyword":
        try:
            provider = get_embedding_provider()
        except EmbeddingsDisabled as exc:
            raise HTTPException(
                409, f"semantic search is disabled ({exc}); use mode=keyword"
            ) from exc
    try:
        result = svc.search(
            session,
            q,
            mode=mode,
            provider=provider,
            limit=limit,
            filters=SearchFilters(
                subject_id=subject_id, week=week, kind=kind, document_id=document_id
            ),
            candidates=settings.search_candidates,
            rrf_k=settings.search_rrf_k,
        )
    except EmbeddingError as exc:
        raise HTTPException(503, f"embedding model unavailable: {exc}") from exc
    return SearchResponse(
        query=result.query,
        mode=result.mode,
        model=provider.model_id if provider else None,
        semantic_candidates=result.semantic_candidates,
        keyword_candidates=result.keyword_candidates,
        results=[_hit_out(h) for h in result.hits],
    )
